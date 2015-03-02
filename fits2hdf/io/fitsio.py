# -*- coding: utf-8 -*-
"""
fitsio.py
=========

FITS I/O for reading and writing to FITS files.
"""

from astropy.io import fits as pf
#import pyfits as pf
from astropy.units import Unit, UnrecognizedUnit
import numpy as np
from datetime import datetime

from ..idi import *
from .. import idi
from .. import unit_conversion
from ..printlog import PrintLog

# A list of keywords that are mandatory to FITS, but should always be calculated
# By the writing program from the data model, and should not be written to HDF5
restricted_header_keywords = {"XTENSION", "BITPIX", "SIMPLE", "PCOUNT", "GCOUNT",
                              "GROUPS", "EXTEND", "TFIELDS", "EXTNAME"}
restricted_table_keywords = {"TDISP", "TUNIT", "TTYPE", "TFORM", "TBCOL",
                             "TNULL", "TSCAL", "TZERO", "NAXIS"}

def fits_format_code_lookup(numpy_dtype, numpy_shape):
    """ Return a FITS format code from a given numpy dtype

    Parameters
    ----------
    numpy_dtype: a numpy dtype object
        Numpy dtype to lookup
    numpy_shape: tuple
        shape of data array to be converted into a FITS format

    Notes
    -----

    FITS format code         Description                     8-bit bytes
    --------------------------------------------------------------------
    L                        logical (Boolean)               1
    X                        bit                             *
    B                        Unsigned byte                   1
    I                        16-bit integer                  2
    J                        32-bit integer                  4
    K                        64-bit integer                  4
    A                        character                       1
    E                        single precision floating point 4
    D                        double precision floating point 8
    C                        single precision complex        8
    M                        double precision complex        16
    P                        array descriptor                8
    Q                        array descriptor                16
    """

    np_type = numpy_dtype.type


    # Get length of array last axis
    fmt_len = 1
    if len(numpy_shape) > 1:
        fmt_len = numpy_shape[-1]

    if np_type is np.string_:
        if numpy_dtype.itemsize == 1:
            return "A"
        else:
            fits_fmt = '%iA' % numpy_dtype.itemsize
            return fits_fmt

    else:
        #TODO: handle special case of uint16,32,64, requires BZERO / scale
        dtype_dict = {
            np.uint8:   'B',
            np.uint16:  'J',    # No native FITS equivalent
            np.uint32:  'K',
            np.uint64:  'K',
            np.int8:    'I',    # No native FITS equivalent
            np.int16:   'I',
            np.int32:   'J',
            np.int64:   'K',
            np.float16: 'E',    # No native FITS equivalent
            np.float32: 'E',
            np.float64: 'D',
            np.complex64: 'C',
            np.complex128: 'M',
            np.bool_:      'L'
        }

        fmt_code = dtype_dict.get(np_type)
        if fmt_len == 1:
            return fmt_code
        else:
            return "%i%s" % (fmt_len, fmt_code)

def write_headers(hduobj, idiobj):
    """ copy headers over from idiobj to hduobj.

    Need to skip values that refer to columns (TUNIT, TDISP), as
    these are necessarily generated by the table creation

    Parameters
    ----------
    hduobj: astropy FITS HDU (ImageHDU, BintableHDU)
        FITS HDU to which to write header values
    idiobj: IdiImageHdu, IdiTableHdu, IdiPrimaryHdu
        HDU object from which to copy headers from
    verbosity: int
        Level of verbosity, none (0) to all (5)
    """

    for key, value in idiobj.header.items():

        try:
            comment = idiobj.header[key+"_COMMENT"]
        except:
            comment = ''

        is_comment = key.endswith("_COMMENT")
        is_table   = key[:5] in restricted_table_keywords
        is_table = is_table or key[:4] == "TDIM" or key == "TFIELDS"

        is_basic = key in restricted_header_keywords
        if is_comment or is_table or is_basic:
            pass
        else:
            hduobj.header[key] = (value, comment)

    for histline in idiobj.history:
        hduobj.header.add_history(histline)

    for commline in idiobj.comment:
        hduobj.header.add_comment(commline)

    hduobj.verify('fix')
    return hduobj

def parse_fits_header(hdul):
    """ Parse a FITS header into something less stupid.

    Parameters
    ----------
    hdul: HDUList
        FITS HDUlist from which to parse the header

    Notes
    -----
    This function takes a fits HDU object and returns:
    header (dict): Dictionary of header values. Header comments
                   are written to [CARDNAME]_COMMENT
    comment (list): Comment cards are parsed and then put into list
                    (order is important)
    history (list): History cards also parsed into a list
    """

    history  = []
    comment = []
    header   = {}

    hdul.verify('fix')

    for card in hdul.header.cards:

        card_id, card_val, card_comment = card
        card_id = card_id.strip()
        comment_id = card_id + "_COMMENT"

        if card_id in (None, '', ' '):
            pass
        elif card_id in restricted_header_keywords:
            pass
        elif card_id[:5] in restricted_table_keywords:
            pass
        elif card_id == "HISTORY":
            history.append(card_val)
        elif card_id == "COMMENT":
            comment.append(card_val)
        else:
            header[card_id] = card_val
            header[comment_id] = card_comment

    return header, comment, history


def read_fits(infile, verbosity=0):
    """
    Read and load contents of a FITS file

    Parameters
    ----------
    infile: str
        File path of input file
    verbosity: int
        Verbosity level of output, 0 (none) to 5 (all)
    """

    pp = PrintLog(verbosity=verbosity)
    ff = pf.open(infile)
    

    hdul_idi = idi.IdiHdulist()

    hdul_idi.fits = ff

    ii = 0
    for hdul_fits in ff:
        if hdul_fits.name in ('', None, ' '):
            hdul_fits.name = "HDU%i" % ii
            ii += 1

        header, history, comment = parse_fits_header(hdul_fits)

        ImageHDU   = pf.hdu.ImageHDU
        PrimaryHDU = pf.hdu.PrimaryHDU
        compHDU    = pf.hdu.CompImageHDU

        if isinstance(hdul_fits, ImageHDU) or isinstance(hdul_fits, PrimaryHDU):
            pp.debug("Adding Image HDU %s" % hdul_fits)
            try:
                if hdul_fits.size == 0:
                    hdul_idi.add_primary_hdu(hdul_fits.name,
                                              header=header, history=history, comment=comment)
                elif hdul_fits.is_image:
                    hdul_idi.add_image_hdu(hdul_fits.name, data=hdul_fits.data[:],
                                           header=header, history=history, comment=comment)
                else:
                    # We have a random group table, yuck
                    hdul_idi.add_table_hdu(hdul_fits.name, data=hdul_fits.data[:],
                                           header=header, history=history, comment=comment)
            except TypeError:
                # Primary groups HDUs can raise this error with no data
                hdul_idi.add_primary_hdu(hdul_fits.name,
                                         header=header, history=history, comment=comment)

        elif isinstance(hdul_fits, compHDU):
            pp.debug("Adding Compressed Image HDU %s" % hdul_fits)
            hdul_idi.add_image_hdu(hdul_fits.name, data=hdul_fits.data[:],
                                   header=header, history=history, comment=comment)
        else:
            pp.debug("Adding Tablular HDU %s" % hdul_fits)
            # Data is tabular
            tbl_data = Table.read(infile, hdu=hdul_fits.name)
            idi_tbl = IdiTableHdu(hdul_fits.name, tbl_data)
            hdul_idi.add_table_hdu(hdul_fits.name,
                                   header=header, data=idi_tbl, history=history, comment=comment)

    return hdul_idi

def create_fits(hdul, verbosity=0):
    """
    Export HDU to FITS file in memory.

    Returns an in-memory HDUlist, does not write to file.

    Parameters
    ----------
    hdul: IdiHduList
        An IDI HDU list object to convert into a pyfits /
        astropy HDUlist() object in memory
    verbosity: int
        verbosity level, 0 (none) to 5 (all)
    """
    pp = PrintLog(verbosity=verbosity)
    # Create a new hdulist
    hdulist = pf.HDUList()

    # Create a new hdu for every item in IdiList
    for name, idiobj in hdul.items():
        pp.debug("%s %s" % (name, idiobj))

        if isinstance(idiobj, IdiPrimaryHdu):
            pp.pp("Creating Primary HDU %s" % idiobj)
            new_hdu = pf.PrimaryHDU()
            new_hdu = write_headers(new_hdu, idiobj)
            hdulist.insert(0, new_hdu)

        elif isinstance(idiobj, IdiImageHdu):
            pp.pp("Creating Image HDU %s" % idiobj)
            if name == "PRIMARY":
                new_hdu = pf.PrimaryHDU()
                new_hdu = write_headers(new_hdu, idiobj)
                new_hdu.data = idiobj.data
                hdulist.insert(0, new_hdu)
            else:
                new_hdu = pf.ImageHDU()
                new_hdu = write_headers(new_hdu, idiobj)
                new_hdu.data = idiobj.data
                new_hdu.name = name
                hdulist.append(new_hdu)

        elif isinstance(idiobj, IdiTableHdu)
            pp.pp("Creating Table HDU %s" % idiobj)
            fits_cols = []
            for cn in idiobj.colnames:
                col = idiobj[cn]
                fits_fmt = fits_format_code_lookup(col.dtype, col.shape)
                fits_unit = unit_conversion.units_to_fits(col.unit)
                fits_col = pf.Column(name=col.name, format=fits_fmt, unit=fits_unit, array=col.data)
                fits_cols.append(fits_col)
            table_def = pf.ColDefs(fits_cols)
            #print table_def
            new_hdu = pf.BinTableHDU.from_columns(table_def)
            new_hdu = write_headers(new_hdu, idiobj)
            new_hdu.name = name
            new_hdu.verify()
            hdulist.append(new_hdu)
            hdulist.verify()


    # Add creation info to history
    now = datetime.now()
    now_str = now.strftime("%Y-%M-%dT%H:%M")
    hdulist[0].header.add_history("File written by fits2hdf %s" %now_str)
    return hdulist



def export_fits(hdul, outfile, verbosity=0):
    """
    Export HDU list to file

    Parameters
    ----------
    hdul: IdiHduList
        HDU list to write to file
    outfile: str
        Filename of ourput file
    verbosity: int
        verbosity of output, 0 (none) to 5 (all)
    """
    fits_hdulist = create_fits(hdul, verbosity=verbosity)
    # Write to file
    fits_hdulist.writeto(outfile, checksum=True, output_verify='fix')
