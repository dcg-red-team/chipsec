#!/usr/bin/python
#CHIPSEC: Platform Security Assessment Framework
#Copyright (c) 2010-2019, Intel Corporation
#
#This program is free software; you can redistribute it and/or
#modify it under the terms of the GNU General Public License
#as published by the Free Software Foundation; Version 2.
#
#This program is distributed in the hope that it will be useful,
#but WITHOUT ANY WARRANTY; without even the implied warranty of
#MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#GNU General Public License for more details.
#
#You should have received a copy of the GNU General Public License
#along with this program; if not, write to the Free Software
#Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301, USA.
#
#Contact information:
#chipsec@intel.com
#



# -------------------------------------------------------------------------------
#
# CHIPSEC: Platform Hardware Security Assessment Framework
# (c) 2010-2019 Intel Corporation
#
# -------------------------------------------------------------------------------

"""
Access to SPI Flash parts

usage:
    >>> read_spi( spi_fla, length )
    >>> write_spi( spi_fla, buf )
    >>> erase_spi_block( spi_fla )
    >>> get_SPI_JEDEC_ID()
    >>> get_SPI_JEDEC_ID_decoded()

.. note::
    !! IMPORTANT:
    Size of the data chunk used in SPI read cycle (in bytes)
    default = maximum 64 bytes (remainder is read in 4 byte chunks)

    If you want to change logic to read SPI Flash in 4 byte chunks:
    SPI_READ_WRITE_MAX_DBC = 4

    @TBD: SPI write cycles operate on 4 byte chunks (not optimized yet)

    Approximate performance (on 2-core SMT Intel Core i5-4300U (Haswell) CPU 1.9GHz):
    SPI read: ~7 sec per 1MB (with DBC=64)
"""

import struct
import sys
import time
import xml.etree.ElementTree as ET

import chipsec.defines
from chipsec.file import *
from chipsec.cfg.common import *
from chipsec.hal import hal_base, mmio
from chipsec.helper import oshelper
from chipsec.hal.spi_jedec_ids import *

SPI_READ_WRITE_MAX_DBC = 64
SPI_READ_WRITE_DEF_DBC = 4
SFDP_HEADER = 0x50444653

SPI_MAX_PR_COUNT  = 5
SPI_FLA_SHIFT     = 12
SPI_FLA_PAGE_MASK = chipsec.defines.ALIGNED_4KB

# agregated SPI Flash commands
HSFCTL_READ_CYCLE  = ( (Cfg.PCH_RCBA_SPI_HSFCTL_FCYCLE_READ<<1) | Cfg.PCH_RCBA_SPI_HSFCTL_FCYCLE_FGO)
HSFCTL_WRITE_CYCLE = ( (Cfg.PCH_RCBA_SPI_HSFCTL_FCYCLE_WRITE<<1) | Cfg.PCH_RCBA_SPI_HSFCTL_FCYCLE_FGO)
HSFCTL_ERASE_CYCLE = ( (Cfg.PCH_RCBA_SPI_HSFCTL_FCYCLE_ERASE<<1) | Cfg.PCH_RCBA_SPI_HSFCTL_FCYCLE_FGO)
HSFCTL_JEDEC_CYCLE = ( (Cfg.PCH_RCBA_SPI_HSFCTL_FCYCLE_JEDEC<<1) | Cfg.PCH_RCBA_SPI_HSFCTL_FCYCLE_FGO)
HSFCTL_SFDP_CYCLE = ( (Cfg.PCH_RCBA_SPI_HSFCTL_FCYCLE_SFDP<<1) | Cfg.PCH_RCBA_SPI_HSFCTL_FCYCLE_FGO)

# !!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
# FGO bit cleared (for safety ;)
# !!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
#HSFCTL_WRITE_CYCLE = ( (Cfg.PCH_RCBA_SPI_HSFCTL_FCYCLE_WRITE<<1) )
#HSFCTL_ERASE_CYCLE = ( (Cfg.PCH_RCBA_SPI_HSFCTL_FCYCLE_ERASE<<1) )

HSFSTS_CLEAR = (Cfg.PCH_RCBA_SPI_HSFSTS_AEL | Cfg.PCH_RCBA_SPI_HSFSTS_FCERR | Cfg.PCH_RCBA_SPI_HSFSTS_FDONE)

#
# Hardware Sequencing Flash Status (HSFSTS)
#
SPI_HSFSTS_OFFSET = 0x04
# HSFSTS bit masks
SPI_HSFSTS_FLOCKDN_MASK = (1 << 15)
SPI_HSFSTS_FDOPSS_MASK  = (1 << 13)

#
# Flash Regions
#

SPI_REGION_NUMBER_IN_FD = 12

FLASH_DESCRIPTOR    = 0
BIOS                = 1
ME                  = 2
GBE                 = 3
PLATFORM_DATA       = 4
FREG5               = 5
FREG6               = 6
FREG7               = 7
EMBEDDED_CONTROLLER = 8
FREG9               = 9
FREG10              = 10
FREG11              = 11

SPI_REGION = {
 FLASH_DESCRIPTOR   : 'FREG0_FLASHD',
 BIOS               : 'FREG1_BIOS',
 ME                 : 'FREG2_ME',
 GBE                : 'FREG3_GBE',
 PLATFORM_DATA      : 'FREG4_PD',
 FREG5              : 'FREG5',
 FREG6              : 'FREG6',
 FREG7              : 'FREG7',
 EMBEDDED_CONTROLLER: 'FREG8_EC',
 FREG9              : 'FREG9',
 FREG10             : 'FREG10',
 FREG11             : 'FREG11'
}

SPI_REGION_NAMES = {
 FLASH_DESCRIPTOR   : 'Flash Descriptor',
 BIOS               : 'BIOS',
 ME                 : 'Intel ME',
 GBE                : 'GBe',
 PLATFORM_DATA      : 'Platform Data',
 FREG5              : 'Flash Region 5',
 FREG6              : 'Flash Region 6',
 FREG7              : 'Flash Region 7',
 EMBEDDED_CONTROLLER: 'Embedded Controller',
 FREG9              : 'Flash Region 9',
 FREG10             : 'Flash Region 10',
 FREG11             : 'Flash Region 11'
}

#
# Flash Descriptor Master Defines
#

MASTER_HOST_CPU_BIOS    = 0
MASTER_ME               = 1
MASTER_GBE              = 2
MASTER_EC               = 3

SPI_MASTER_NAMES = {
 MASTER_HOST_CPU_BIOS : 'CPU',
 MASTER_ME            : 'ME',
 MASTER_GBE           : 'GBe',
 MASTER_EC            : 'EC'
}


# @TODO: DEPRECATED
def get_SPI_region(flreg):
    range_base  = (flreg & Cfg.PCH_RCBA_SPI_FREGx_BASE_MASK) << SPI_FLA_SHIFT
    range_limit = ((flreg & Cfg.PCH_RCBA_SPI_FREGx_LIMIT_MASK) >> 4)
    range_limit |= SPI_FLA_PAGE_MASK
    return (range_base, range_limit)

class SpiRuntimeError (RuntimeError):
    pass

class SpiAccessError (RuntimeError):
    pass


class SPI(hal_base.HALBase):
    # XML device definition files stuff
    XML_ROOT_NAME         = "SFDP"
    XML_COMMAND_NODE_TAG  = "register"
    XML_FIELD_NODE_TAG    = "field"

    def __init__(self, cs):
        super(SPI, self).__init__(cs)
        self.mmio = mmio.MMIO(cs)
        self.rcba_spi_base = self.get_SPI_MMIO_base()
        # We try to map SPIBAR in the process memory, this will increase the
        # speed of MMIO access later on.
        try:
            self.cs.helper.map_io_space(self.rcba_spi_base, Cfg.SPI_MMIO_BASE_LENGTH, 0)
        except oshelper.UnimplementedAPIError:
            pass

        # Reading definitions of SPI flash controller registers
        # which are required to send SPI cycles once for performance reasons
        self.hsfs_off   = int(self.cs.get_register_def("HSFS")['offset'],16)
        self.hsfc_off   = int(self.cs.get_register_def("HSFC")['offset'],16)
        self.faddr_off  = int(self.cs.get_register_def("FADDR")['offset'],16)
        self.fdata0_off = int(self.cs.get_register_def("FDATA0")['offset'],16)
        self.fdata1_off = int(self.cs.get_register_def("FDATA1")['offset'],16)
        self.fdata2_off = int(self.cs.get_register_def("FDATA2")['offset'],16)
        self.fdata3_off = int(self.cs.get_register_def("FDATA3")['offset'],16)
        self.fdata4_off = int(self.cs.get_register_def("FDATA4")['offset'],16)
        self.fdata5_off = int(self.cs.get_register_def("FDATA5")['offset'],16)
        self.fdata6_off = int(self.cs.get_register_def("FDATA6")['offset'],16)
        self.fdata7_off = int(self.cs.get_register_def("FDATA7")['offset'],16)
        self.fdata8_off = int(self.cs.get_register_def("FDATA8")['offset'],16)
        self.fdata9_off = int(self.cs.get_register_def("FDATA9")['offset'],16)
        self.fdata10_off = int(self.cs.get_register_def("FDATA10")['offset'],16)
        self.fdata11_off = int(self.cs.get_register_def("FDATA11")['offset'],16)
        self.fdata12_off = int(self.cs.get_register_def("FDATA12")['offset'],16)
        self.fdata13_off = int(self.cs.get_register_def("FDATA13")['offset'],16)
        self.fdata14_off = int(self.cs.get_register_def("FDATA14")['offset'],16)
        self.fdata15_off = int(self.cs.get_register_def("FDATA15")['offset'],16)
        self.bios_ptinx  = int(self.cs.get_register_def("BIOS_PTINX")['offset'],16)
        self.bios_ptdata  = int(self.cs.get_register_def("BIOS_PTDATA")['offset'],16)

        if logger().HAL:
            logger().log( "[spi] Reading SPI flash controller registers definitions:" )
            logger().log( "      HSFC   offset = 0x%04X" % self.hsfc_off )
            logger().log( "      HSFS   offset = 0x%04X" % self.hsfs_off )
            logger().log( "      FADDR  offset = 0x%04X" % self.faddr_off )
            logger().log( "      FDATA0 offset = 0x%04X" % self.fdata0_off )

    # Fallback option when XML config is not available: using hardcoded config
    def get_SPI_MMIO_base_fallback(self):
        reg_value = self.cs.pci.read_dword( Cfg.SPI_MMIO_BUS, Cfg.SPI_MMIO_DEV, Cfg.SPI_MMIO_FUN, Cfg.SPI_MMIO_REG_OFFSET )
        spi_base = ((reg_value >> Cfg.SPI_BASE_ADDR_SHIFT) << Cfg.SPI_BASE_ADDR_SHIFT) + Cfg.SPI_MMIO_BASE_OFFSET
        if logger().HAL: logger().log( "[spi] SPI MMIO base: 0x%016X (assuming below 4GB)" % spi_base )
        return spi_base

    def get_SPI_MMIO_base(self):
        if self.mmio.is_MMIO_BAR_defined('SPIBAR'):
            (spi_base,spi_size) = self.mmio.get_MMIO_BAR_base_address('SPIBAR')
        else:
            spi_base = self.get_SPI_MMIO_base_fallback()
        if logger().HAL: logger().log( "[spi] SPI MMIO base: 0x%016X (assuming below 4GB)" % spi_base )
        return spi_base

    def spi_reg_read( self, reg, size=4 ):
        return self.mmio.read_MMIO_reg(self.rcba_spi_base, reg, size)

    def spi_reg_write( self, reg, value, size=4 ):
        return self.mmio.write_MMIO_reg(self.rcba_spi_base, reg, value, size)


    def get_SPI_region( self, spi_region_id ):
        freg_name = SPI_REGION[ spi_region_id ]
        if not self.cs.is_register_defined(freg_name):
            return (None, None, None)
        freg = self.cs.read_register(freg_name)
        # Region Base corresponds to FLA bits 24:12
        range_base  = self.cs.get_register_field(freg_name, freg, 'RB' ) << SPI_FLA_SHIFT
        # Region Limit corresponds to FLA bits 24:12
        range_limit = self.cs.get_register_field(freg_name, freg, 'RL' ) << SPI_FLA_SHIFT
        # FLA bits 11:0 are assumed to be FFFh for the limit comparison
        range_limit |= SPI_FLA_PAGE_MASK
        return (range_base, range_limit, freg)

    # all_regions = True : return all SPI regions
    # all_regions = False: return only available SPI regions (limit >= base)
    def get_SPI_regions( self, all_regions=True):
        spi_regions = {}
        for r in SPI_REGION:
            (range_base, range_limit, freg) = self.get_SPI_region( r )
            if range_base is None:
                continue
            if all_regions or (range_limit >= range_base):
                range_size = range_limit - range_base + 1
                spi_regions[r] = (range_base, range_limit, range_size, SPI_REGION_NAMES[r], freg)
        return spi_regions

    def get_SPI_Protected_Range( self, pr_num ):
        if pr_num > SPI_MAX_PR_COUNT:
            return None

        pr_name = 'PR%x'%pr_num
        pr_j_reg = int(self.cs.get_register_def(pr_name)['offset'],16)
        pr_j = self.cs.read_register(pr_name)

        # Protected Range Base corresponds to FLA bits 24:12
        base  = self.cs.get_register_field(pr_name, pr_j, 'PRB' ) << SPI_FLA_SHIFT
        # Protected Range Limit corresponds to FLA bits 24:12
        limit = self.cs.get_register_field(pr_name, pr_j, 'PRL' ) << SPI_FLA_SHIFT

        wpe = (0 != self.cs.get_register_field(pr_name, pr_j, 'WPE' ))
        rpe = (0 != self.cs.get_register_field(pr_name, pr_j, 'RPE' ))

        # Check if this is a valid PRx config
        if wpe or rpe:
            # FLA bits 11:0 are assumed to be FFFh for the limit comparison
            limit |= SPI_FLA_PAGE_MASK

        return (base,limit,wpe,rpe,pr_j_reg,pr_j)

    ##############################################################################################################
    # SPI configuration
    ##############################################################################################################

    def display_SPI_Flash_Descriptor( self ):
        logger().log( "============================================================" )
        logger().log( "SPI Flash Descriptor" )
        logger().log( "------------------------------------------------------------" )
        logger().log( "\nFlash Signature and Descriptor Map:" )
        for j in range(5):
            self.cs.write_register('FDOC', (Cfg.PCH_RCBA_SPI_FDOC_FDSS_FSDM|(j<<2)))
            fdod = self.cs.read_register('FDOD')
            logger().log( "%08X" % fdod )

        logger().log( "\nComponents:" )
        for j in range(3):
            self.cs.write_register('FDOC', (Cfg.PCH_RCBA_SPI_FDOC_FDSS_COMP|(j<<2)))
            fdod = self.cs.read_register('FDOD')
            logger().log( "%08X" % fdod )

        logger().log( "\nRegions:" )
        for j in range(5):
            self.cs.write_register('FDOC', (Cfg.PCH_RCBA_SPI_FDOC_FDSS_REGN|(j<<2)))
            fdod = self.cs.read_register('FDOD')
            logger().log( "%08X" % fdod )

        logger().log( "\nMasters:" )
        for j in range(3):
            self.cs.write_register('FDOC', (Cfg.PCH_RCBA_SPI_FDOC_FDSS_MSTR|(j<<2)))
            fdod = self.cs.read_register('FDOD')
            logger().log( "%08X" % fdod )


    def display_SPI_opcode_info( self ):
        logger().log( "============================================================" )
        logger().log( "SPI Opcode Info" )
        logger().log( "------------------------------------------------------------" )
        preop = self.cs.read_register( 'PREOP' )
        logger().log( "PREOP : 0x%04X" % preop )
        optype = self.cs.read_register('OPTYPE' )
        logger().log( "OPTYPE: 0x%04X" % optype )
        opmenu_lo = self.cs.read_register('OPMENU_LO' )
        opmenu_hi = self.cs.read_register('OPMENU_HI' )
        opmenu = ((opmenu_hi << 32)|opmenu_lo)
        logger().log( "OPMENU: 0x%016X" % opmenu )
        logger().log('')
        preop0 = preop&0xFF
        preop1 = (preop>>8)&0xFF
        logger().log( "Prefix Opcode 0 = 0x%02X" % preop0 )
        logger().log( "Prefix Opcode 1 = 0x%02X" % preop1 )

        logger().log( "------------------------------------------------------------" )
        logger().log( "Opcode # | Opcode | Optype | Description" )
        logger().log( "------------------------------------------------------------" )
        for j in range(8):
            optype_j = ((optype >> j*2) & 0x3)
            if (Cfg.PCH_RCBA_SPI_OPTYPE_RDNOADDR == optype_j):
                desc = 'SPI read cycle without address'
            elif (Cfg.PCH_RCBA_SPI_OPTYPE_WRNOADDR == optype_j):
                desc = 'SPI write cycle without address'
            elif (Cfg.PCH_RCBA_SPI_OPTYPE_RDADDR == optype_j):
                desc = 'SPI read cycle with address'
            elif (Cfg.PCH_RCBA_SPI_OPTYPE_WRADDR == optype_j):
                desc = 'SPI write cycle with address'
            logger().log( "Opcode%d  | 0x%02X   | %X      | %s " % (j,((opmenu >> j*8) & 0xFF),optype_j,desc) )

    def display_SPI_Flash_Regions( self ):
        logger().log( "------------------------------------------------------------" )
        logger().log( "Flash Region             | FREGx Reg | Base     | Limit     " )
        logger().log( "------------------------------------------------------------" )
        regions = self.get_SPI_regions()
        for region_id, region in regions.iteritems():
            base, limit, size, name, freg = region
            logger().log( '%d %-022s | %08X  | %08X | %08X ' % (region_id, name, freg, base, limit) )

    def display_BIOS_region( self ):
        bfpreg = self.cs.read_register('BFPR' )
        base  = self.cs.get_register_field('BFPR', bfpreg, 'PRB' ) << SPI_FLA_SHIFT
        limit = self.cs.get_register_field('BFPR', bfpreg, 'PRL' ) << SPI_FLA_SHIFT
        limit |= SPI_FLA_PAGE_MASK
        logger().log( "BIOS Flash Primary Region" )
        logger().log( "------------------------------------------------------------" )
        logger().log( "BFPREG = %08X:" % bfpreg )
        logger().log( "  Base  : %08X" % base )
        logger().log( "  Limit : %08X" % limit )

    def display_SPI_Ranges_Access_Permissions( self ):
        logger().log( "SPI Flash Region Access Permissions" )
        logger().log( "------------------------------------------------------------" )
        fracc = self.cs.read_register('FRAP')
        self.cs.print_register('FRAP', fracc)
        brra  = self.cs.get_register_field('FRAP', fracc, 'BRRA' )
        brwa  = self.cs.get_register_field('FRAP', fracc, 'BRWA' )
        bmrag = self.cs.get_register_field('FRAP', fracc, 'BMRAG' )
        bmwag = self.cs.get_register_field('FRAP', fracc, 'BMWAG' )
        logger().log( '' )
        logger().log( "BIOS Region Write Access Grant (%02X):" % bmwag )
        regions = self.get_SPI_regions()
        for region_id in regions:
            logger().log( "  %-12s: %1d" % (SPI_REGION[region_id], (0 != bmwag&(1<<region_id))) )
        logger().log( "BIOS Region Read Access Grant (%02X):" % bmrag )
        for region_id in regions:
            logger().log( "  %-12s: %1d" % (SPI_REGION[region_id ], (0 != bmrag&(1<<region_id))) )
        logger().log( "BIOS Region Write Access (%02X):" % brwa )
        for region_id in regions:
            logger().log( "  %-12s: %1d" % (SPI_REGION[ region_id ], (0 != brwa&(1<<region_id))) )
        logger().log( "BIOS Region Read Access (%02X):" % brra )
        for region_id in regions:
            logger().log( "  %-12s: %1d" % (SPI_REGION[ region_id ], (0 != brra&(1<<region_id))) )

    def display_SPI_Protected_Ranges( self ):
        logger().log( "SPI Protected Ranges" )
        logger().log( "------------------------------------------------------------" )
        logger().log( "PRx (offset) | Value    | Base     | Limit    | WP? | RP?" )
        logger().log( "------------------------------------------------------------" )
        for j in range(5):
            (base,limit,wpe,rpe,pr_reg_off,pr_reg_value) = self.get_SPI_Protected_Range( j )
            logger().log( "PR%d (%02X)     | %08X | %08X | %08X | %d   | %d " % (j,pr_reg_off,pr_reg_value,base,limit,wpe,rpe) )

    def display_SPI_map( self ):
        logger().log( "============================================================" )
        logger().log( "SPI Flash Map" )
        logger().log( "------------------------------------------------------------" )
        logger().log('')
        self.display_BIOS_region()
        logger().log('')
        self.display_SPI_Flash_Regions()
        logger().log('')
        self.display_SPI_Flash_Descriptor()
        logger().log('')
        self.display_SPI_opcode_info()
        logger().log('')
        logger().log( "============================================================" )
        logger().log( "SPI Flash Protection" )
        logger().log( "------------------------------------------------------------" )
        logger().log('')
        self.display_SPI_Ranges_Access_Permissions()
        logger().log('')
        logger().log( "BIOS Region Write Protection" )
        logger().log( "------------------------------------------------------------" )
        self.display_BIOS_write_protection()
        logger().log('')
        self.display_SPI_Protected_Ranges()
        logger().log('')


    ##############################################################################################################
    # BIOS Write Protection
    ##############################################################################################################

    def display_BIOS_write_protection( self ):
        if self.cs.is_register_defined('BC'):
             reg_value = self.cs.read_register('BC')
             self.cs.print_register('BC', reg_value )
        else:
            if logger().HAL: logger().error( "Could not locate the definition of 'BIOS Control' register.." )


    def disable_BIOS_write_protection( self ):
        if logger().HAL: self.display_BIOS_write_protection()
        ble    = self.cs.get_control('BiosLockEnable' )
        bioswe = self.cs.get_control('BiosWriteEnable' )
        smmbwp = self.cs.get_control('SmmBiosWriteProtection' )

        if smmbwp == 1:
            if logger().HAL: logger().log( "[spi] SMM BIOS write protection (SmmBiosWriteProtection) is enabled" )

        if bioswe == 1:
            if logger().HAL: logger().log( "[spi] BIOS write protection (BiosWriteEnable) is not enabled" )
            return True
        elif ble == 0:
            if logger().HAL: logger().log( "[spi] BIOS write protection is enabled but not locked. Disabling.." )
        else: # bioswe == 0 and ble == 1
            if logger().HAL: logger().log( "[spi] BIOS write protection is enabled. Attempting to disable.." )

        # Set BiosWriteEnable control bit
        self.cs.set_control('BiosWriteEnable', 1 )

        # read BiosWriteEnable back to check if BIOS writes are enabled
        bioswe = self.cs.get_control('BiosWriteEnable' )

        if logger().HAL: self.display_BIOS_write_protection()
        if logger().HAL: logger().log_important( "BIOS write protection is %s (BiosWriteEnable = %d)" % ('disabled' if bioswe else 'still enabled', bioswe) )

        return (bioswe==1)


    ##############################################################################################################
    # SPI Controller access functions
    ##############################################################################################################

    def _wait_SPI_flash_cycle_done(self):
        if logger().HAL: logger().log( "[spi] wait for SPI cycle ready/done.." )

        for i in range(1000):
            #time.sleep(0.001)
            hsfsts = self.spi_reg_read( self.hsfs_off, 1 )

            #cycle_done = (hsfsts & Cfg.Cfg.PCH_RCBA_SPI_HSFSTS_FDONE) and (0 == (hsfsts & Cfg.PCH_RCBA_SPI_HSFSTS_SCIP))
            cycle_done = not (hsfsts & Cfg.PCH_RCBA_SPI_HSFSTS_SCIP)
            if cycle_done:
                break

        if not cycle_done:
            if logger().HAL: logger().log( "[spi] SPI cycle still in progress. Waiting 0.1 sec.." )
            time.sleep(0.1)
            hsfsts = self.spi_reg_read( self.hsfs_off, 1 )
            cycle_done = not (hsfsts & Cfg.PCH_RCBA_SPI_HSFSTS_SCIP)

        if cycle_done:
            if logger().HAL: logger().log( "[spi] clear FDONE/FCERR/AEL bits.." )
            self.spi_reg_write( self.hsfs_off, HSFSTS_CLEAR, 1 )
            hsfsts = self.spi_reg_read( self.hsfs_off, 1 )
            cycle_done = not ((hsfsts & Cfg.PCH_RCBA_SPI_HSFSTS_AEL) or (hsfsts & Cfg.PCH_RCBA_SPI_HSFSTS_FCERR))

        if logger().HAL: logger().log( "[spi] HSFS: 0x%02X" % hsfsts )

        return cycle_done

    def _send_spi_cycle(self, hsfctl_spi_cycle_cmd, dbc, spi_fla ):
        if logger().HAL: logger().log( "[spi] > send SPI cycle 0x%X to address 0x%08X.." % (hsfctl_spi_cycle_cmd, spi_fla) )

        # No need to check for SPI cycle DONE status before each cycle
        # DONE status is checked once before entire SPI operation

        self.spi_reg_write( self.faddr_off, (spi_fla & Cfg.PCH_RCBA_SPI_FADDR_MASK) )
        # Other options ;)
        #chipsec.chipset.write_register( self.cs, "FADDR", (spi_fla & Cfg.PCH_RCBA_SPI_FADDR_MASK) )
        #write_MMIO_reg( self.cs, spi_base, self.faddr_off, (spi_fla & Cfg.PCH_RCBA_SPI_FADDR_MASK) )
        #self.cs.mem.write_physical_mem_dword( spi_base + self.faddr_off, (spi_fla & Cfg.PCH_RCBA_SPI_FADDR_MASK) )

        if logger().HAL:
            _faddr = self.spi_reg_read( self.faddr_off )
            logger().log( "[spi] FADDR: 0x%08X" % _faddr )

        if logger().HAL: logger().log( "[spi] SPI cycle GO (DBC <- 0x%02X, HSFC <- 0x%X)" % (dbc, hsfctl_spi_cycle_cmd) )

        if ( HSFCTL_ERASE_CYCLE != hsfctl_spi_cycle_cmd ):
            self.spi_reg_write( self.hsfc_off + 0x1, dbc, 1 )

        self.spi_reg_write( self.hsfc_off, hsfctl_spi_cycle_cmd, 1 )
        #self.spi_reg_write( self.hsfc_off, ((dbc<<8)|hsfctl_spi_cycle_cmd), 2 )

        # Read HSFC back (logging only)
        if logger().HAL:
            _hsfc = self.spi_reg_read( self.hsfc_off, 1 )
            logger().log( "[spi] HSFC: 0x%04X" % _hsfc )

        cycle_done = self._wait_SPI_flash_cycle_done()
        if not cycle_done:
            logger().warn( "SPI cycle not done" )
        else:
            if logger().HAL: logger().log( "[spi] < SPI cycle done" )

        return cycle_done

    def check_hardware_sequencing(self):
        # Test if the flash decriptor is valid (and hardware sequencing enabled)
        fdv = self.cs.read_register_field('HSFS', 'FDV')
        if fdv == 0:
            logger().error("HSFS.FDV is 0, hardware sequencing is disabled")
            raise SpiRuntimeError("Chipset does not support hardware sequencing")

    #
    # SPI Flash operations
    #

    def read_spi_to_file(self, spi_fla, data_byte_count, filename ):
        buf = self.read_spi( spi_fla, data_byte_count )
        if buf is None:
            return None
        if filename is not None:
            write_file( filename, struct.pack('c'*len(buf), *buf) )
        else:
            chipsec.logger.print_buffer( buf, 16 )
        return buf

    def write_spi_from_file(self, spi_fla, filename ):
        buf = read_file( filename )
        return self.write_spi( spi_fla, struct.unpack('c'*len(buf), buf) )
        #return self.write_spi( spi_fla, struct.unpack('B'*len(buf), buf) )

    def read_spi(self, spi_fla, data_byte_count ):

        self.check_hardware_sequencing()

        buf = []
        dbc = SPI_READ_WRITE_DEF_DBC
        if (data_byte_count >= SPI_READ_WRITE_MAX_DBC):
            dbc = SPI_READ_WRITE_MAX_DBC

        n = data_byte_count / dbc
        r = data_byte_count % dbc
        if logger().UTIL_TRACE or logger().HAL:
            logger().log( "[spi] reading 0x%x bytes from SPI at FLA = 0x%X (in %d 0x%x-byte chunks + 0x%x-byte remainder)" % (data_byte_count, spi_fla, n, dbc, r) )

        cycle_done = self._wait_SPI_flash_cycle_done()
        if not cycle_done:
            logger().error( "SPI cycle not ready" )
            return None

        for i in range(n):
            if logger().HAL:
                logger().log( "[spi] reading chunk %d of 0x%x bytes from 0x%X" % (i, dbc, spi_fla + i*dbc) )
            if not self._send_spi_cycle( HSFCTL_READ_CYCLE, dbc-1, spi_fla + i*dbc ):
                logger().error( "SPI flash read failed" )
            else:
                for fdata_idx in range(0,dbc/4):
                    dword_value = self.spi_reg_read( self.fdata0_off + fdata_idx*4 )
                    if logger().HAL:
                        logger().log( "[spi] FDATA00 + 0x%x: 0x%X" % (fdata_idx*4, dword_value) )
                    buf += [ chr((dword_value>>(8*j))&0xff) for j in range(4) ]
                    #buf += tuple( struct.pack("I", dword_value) )
        if (0 != r):
            if logger().HAL:
                logger().log( "[spi] reading remaining 0x%x bytes from 0x%X" % (r, spi_fla + n*dbc) )
            if not self._send_spi_cycle( HSFCTL_READ_CYCLE, r-1, spi_fla + n*dbc ):
                logger().error( "SPI flash read failed" )
            else:
                t = 4
                n_dwords = (r+3)/4
                for fdata_idx in range(0, n_dwords):
                    dword_value = self.spi_reg_read( self.fdata0_off + fdata_idx*4 )
                    if logger().HAL:
                        logger().log( "[spi] FDATA00 + 0x%x: 0x%08X" % (fdata_idx*4, dword_value) )
                    if (fdata_idx == (n_dwords-1)) and (0 != r%4):
                        t = r%4
                    buf += [ chr((dword_value >> (8*j)) & 0xff) for j in range(t) ]

        if logger().HAL:
            logger().log( "[spi] buffer read from SPI:" )
            chipsec.logger.print_buffer( buf )

        return buf

    def write_spi(self, spi_fla, buf ):

        self.check_hardware_sequencing()

        write_ok = True
        data_byte_count = len(buf)
        dbc = 4
        n = data_byte_count / dbc
        r = data_byte_count % dbc
        if logger().UTIL_TRACE or logger().HAL:
            logger().log( "[spi] writing 0x%x bytes to SPI at FLA = 0x%X (in %d 0x%x-byte chunks + 0x%x-byte remainder)" % (data_byte_count, spi_fla, n, dbc, r) )

        cycle_done = self._wait_SPI_flash_cycle_done()
        if not cycle_done:
            logger().error( "SPI cycle not ready" )
            return None

        for i in range(n):
            if logger().UTIL_TRACE or logger().HAL:
                logger().log( "[spi] writing chunk %d of 0x%x bytes to 0x%X" % (i, dbc, spi_fla + i*dbc) )
            dword_value = (ord(buf[i*dbc + 3]) << 24) | (ord(buf[i*dbc + 2]) << 16) | (ord(buf[i*dbc + 1]) << 8) | ord(buf[i*dbc])
            if logger().HAL:
                logger().log( "[spi] in FDATA00 = 0x%08x" % dword_value )
            self.spi_reg_write( self.fdata0_off, dword_value )
            if not self._send_spi_cycle( HSFCTL_WRITE_CYCLE, dbc-1, spi_fla + i*dbc ):
                write_ok = False
                logger().error( "SPI flash write cycle failed" )

        if (0 != r):
            if logger().UTIL_TRACE or logger().HAL:
                logger().log( "[spi] writing remaining 0x%x bytes to FLA = 0x%X" % (r, spi_fla + n*dbc) )
            dword_value = 0
            for j in range(r):
                dword_value |= (ord(buf[n*dbc + j]) << 8*j)
            if logger().HAL:
                logger().log( "[spi] in FDATA00 = 0x%08x" % dword_value )
            self.spi_reg_write( self.fdata0_off, dword_value )
            if not self._send_spi_cycle( HSFCTL_WRITE_CYCLE, r-1, spi_fla + n*dbc ):
                write_ok = False
                logger().error( "SPI flash write cycle failed" )

        return write_ok

    def erase_spi_block(self, spi_fla ):

        self.check_hardware_sequencing()

        if logger().UTIL_TRACE or logger().HAL:
            logger().log( "[spi] Erasing SPI Flash block @ 0x%X" % spi_fla )

        cycle_done = self._wait_SPI_flash_cycle_done()
        if not cycle_done:
            logger().error( "SPI cycle not ready" )
            return None

        erase_ok = self._send_spi_cycle( HSFCTL_ERASE_CYCLE, 0, spi_fla )
        if not erase_ok:
            logger().error( "SPI Flash erase cycle failed" )

        return erase_ok

    #
    # SPI SFDP operations
    #
    def parse_dev_file(self, file_path):
        return ET.parse(file_path)

    def check_def_file(self, filename):
        base_name  = os.path.basename(filename)
        full_path = os.path.join( chipsec.file.get_main_dir(), 'chipsec/cfg', base_name )

        if not os.path.exists(full_path):
            self.logger.error("The file %s does not exist!" % base_name)
            return False
        else:
            if logger().VERBOSE: self.logger.log("checking file {0} ...".format(base_name))
            try:
                tree = self.parse_dev_file(full_path)
            except:
                logger().log( "Failed parsing the sfdp.xml file provided")
                return False
            if tree.getroot().tag == self.XML_ROOT_NAME :
                if logger().VERBOSE: self.logger.log("file {0} looks OK ...".format(base_name))
                return full_path
            else:
                self.logger.error("The file {0} has an inappropriate structure!".format(base_name))
                return False

    def get_formated_list_str(self, _list):
        return "[ " + ", ".join("0x{0:02x}".format(int(i)) for i in _list) + " ]"

    def show_command_result(self, command, result):
        self.logger.log("    name: {0:<20} reg_value: {1} : {2:s}".format(
            command.attrib["name"],
            self.get_formated_list_str(result),
            command.attrib["desc"]))

        # look if command in XML dev file has some <field> childs
        for child in command:
            if child.tag and child.tag == self.XML_FIELD_NODE_TAG:
                # mask all found bit fields...
                byte   = int(result[0])
                bitno  = int(child.attrib["bit"])
                length = int(child.attrib["size"])
                mask   = (2 ** length) - 1
                bitval = (byte & (mask << bitno)) >> bitno

                # ...and self.logger.log them
                self.logger.log("\tfield: {0:<25} value: {1:<12s} \t: {2:s}".format(
                    child.attrib["name"],
                    "0x{0:02x}".format(bitval),
                    #"0b{0:0{width}b}".format(bitval, width=length),
                    child.attrib["desc"]))

    def get_SPI_SFDP(self):
        ret = False
        for component in range(0,2):
            logger().log( "Scanning for Flash device %d" % (component+1))
            offset = 0x0000
            offset = offset | (component << 14)
            self.spi_reg_write(self.bios_ptinx, offset)
            self.spi_reg_read(self.bios_ptinx)
            sfdp_signature = self.spi_reg_read(self.bios_ptdata)
            if sfdp_signature == SFDP_HEADER:
                logger().log( "  * Found valid SFDP header for Flash device %d" % (component+1))
                ret = True
            else:
                logger().log ( "  * Didn't find a valid SFDP header for Flash device %d" % (component+1))
                continue
            # Increment offset to read second dword of SFDP header structure
            offset += 0x4
            self.spi_reg_write(self.bios_ptinx, offset)
            self.spi_reg_read(self.bios_ptinx)
            sfdp_data = self.spi_reg_read(self.bios_ptdata)
            sfdp_minor_version = sfdp_data & 0xFF
            sfdp_major_version = ( sfdp_data  >> 8) & 0xFF
            logger().log( "    SFDP version number: %s.%s" % (sfdp_major_version, sfdp_minor_version))
            num_of_param_headers = ((sfdp_data >> 16) & 0xFF) +1
            logger().log( "    Number of parameter headers: %d" % num_of_param_headers)
            offset = 0x0000
            offset = offset | (component << 14)
            # Set offset to read 1st Parameter Table in the SFDP header structure
            offset = offset | 0x1000
            self.spi_reg_write(self.bios_ptinx, offset)
            self.spi_reg_read(self.bios_ptinx)
            parameter_1 = self.spi_reg_read(self.bios_ptdata)
            param1_minor_version = (parameter_1 >> 8) & 0xFF
            param1_major_version = (parameter_1 >> 16) & 0xFF
            param1_length = (parameter_1 >> 24) & 0xFF
            logger().log( "  * Parameter Header 1 (JEDEC)" )
            logger().log( "    ** Parameter version number: %s.%s" % (param1_major_version, param1_minor_version))
            logger().log( "    ** Parameter length in double words: %s" % hex(param1_length))
            if (num_of_param_headers > 1) and self.cs.register_has_field( 'HSFS', 'FCYCLE' ):
                self.check_hardware_sequencing()
                self.spi_reg_write( self.fdata12_off, 0x00000000 )
                self.spi_reg_write( self.fdata13_off, 0x00000000 )
                self.spi_reg_write( self.fdata14_off, 0x00000000 )
                self.spi_reg_write( self.fdata15_off, 0x00000000 )
                if not self._send_spi_cycle( HSFCTL_SFDP_CYCLE, 0x3F, 0 ):
                    logger().error( 'SPI SFDP signature cycle failed' )
                    continue
                pTable_offset_list = []
                pTable_length = []
                # Calculate which fdata_offset registers to read, based on number of parameter headers present
                for i in range(1,num_of_param_headers):
                    logger().log( "  * Parameter Header:%d" % (i+1) )
                    data_reg_1 = "self.fdata" + str(2+(2*i)) + "_off"
                    data_reg_2 = "self.fdata" + str(2+(2*i)+1) + "_off"
                    data_dword_1 = self.spi_reg_read( eval(data_reg_1))
                    data_dword_2 = self.spi_reg_read( eval(data_reg_2))
                    id_manuf = (data_dword_2 & 0xFF000000) >> 16 | (data_dword_1 & 0xFF)
                    param_minor_version = (data_dword_1 >> 8) & 0xFF
                    param_major_version = (data_dword_1 >> 16) & 0xFF
                    param_length = (data_dword_1 >> 24) & 0xFF
                    param_table_pointer = (data_dword_2 & 0x00FFFFFF)
                    logger().log( "    ** Parameter version number: %s.%s" % (param_major_version, param_minor_version))
                    logger().log( "    ** Pramaeter length in double words: %s" % hex(param_length))
                    logger().log( "    ** Parameter ID: %s" % hex(id_manuf))
                    logger().log( "    ** Parameter Table Pointer(byte address): %s " % hex(param_table_pointer))
                    pTable_offset_list.append(param_table_pointer)
                    pTable_length.append(param_length)
            offset = 0
            offset = offset | (component << 14)
            # Set offset to read 1st Parameter table ( JEDEC Basic Flash Parameter Table) content and Parse it
            offset = offset | 0x2000
            logger().log( "                                ")
            logger().log( "  * 1'st Parameter Table Content ")
            self.dev_file = self.check_def_file("sfdp.xml")
            if self.dev_file is False:
                continue
            tree = self.parse_dev_file(self.dev_file)
            root = tree.getroot()
            count = 0
            for child in root:
                if ( count >= param1_length):
                    break
                self.spi_reg_write(self.bios_ptinx, offset)
                self.spi_reg_read(self.bios_ptinx)
                sfdp_data = self.spi_reg_read(self.bios_ptdata)
                count +=1
                offset +=4
                self.show_command_result(child, [sfdp_data])

    #
    # SPI JEDEC ID operations
    #

    def get_SPI_JEDEC_ID(self):

        if self.cs.register_has_field( 'HSFS', 'FCYCLE' ):
            self.check_hardware_sequencing()

            if not self._send_spi_cycle( HSFCTL_JEDEC_CYCLE, 4, 0 ):
                logger().error( 'SPI JEDEC ID cycle failed' )
            id = self.spi_reg_read( self.fdata0_off )
        else:
            return False

        return ((id & 0xFF) << 16) | (id & 0xFF00) | ( (id >> 16) & 0xFF )

    def get_SPI_JEDEC_ID_decoded(self):

        jedec_id = self.get_SPI_JEDEC_ID()
        if jedec_id is False:
            return (False, 0 ,0)
        manu = JEDEC_ID.MANUFACTURER.get((jedec_id >> 16) & 0xff, 'Unknown')
        part = JEDEC_ID.DEVICE.get( jedec_id, 'Unknown')

        return (jedec_id, manu, part)
