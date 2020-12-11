#!/usr/bin/env python
"""
Read and plots active (i.e. non-zero) signals from SIEMENS advanced physiological log / DICOM files
(>=R013, >=VD13A)

This function expects to find either a combination of individual logfiles (*_ECG.log, *_RESP.log,
*_PULS.log, *_EXT.log, *_Info.log) generated by >=R013 sequences, or a single encoded "_PHYSIO" DICOM
file generated by >=R015 sequences

You can also import the internal 'readphysio` library function to obtain active physio traces for ECG1,
ECG2, ECG3, ECG4, RESP, PULS, EXT and EXT2 signals:

physio['UUID']:     unique identifier string for this measurement
physio['SliceMap']: [2 x Volumes x Slices]  [1:2,:,:] = start & finish time stamp of each volume/slice
physio['ACQ']:      [total scan time x 1]   True if acquisition is active at this time; False if not
physio['ECG1']:     [total scan time x 1]   ECG signal on this channel
physio['ECG2']:     [total scan time x 1]   [..]
physio['ECG3']:     [total scan time x 1]   [..]
physio['ECG4']:     [total scan time x 1]   [..]
physio['RESP']:     [total scan time x 1]   RESP signal on this channel
physio['PULS']:     [total scan time x 1]   PULS signal on this channel
physio['EXT']:      [total scan time x 1]   True if EXT signal detected; False if not
physio['EXT2']:     [total scan time x 1]   True if EXT2 signal detected; False if not

The unit of time is clock ticks (2.5 ms per tick).
"""


import struct
import logging
import numpy as np
import matplotlib.pyplot as plt
from typing import Union
from pydicom import dcmread, tag
from pathlib import Path
try:
    from bidscoin import bids
except ImportError:
    import bids         # This should work if bidscoin was not pip-installed

# This is the file format this function expects; must match log file version
expectedversion = 'EJA_1'

# Set-up logging
LOGGER = logging.getLogger('bidscoin')
bids.setup_logging()


def readparsefile(fn: Union[bytes,Path], logdatatype, firsttime, expectedsamples) -> tuple:
    """
    Read and parse physiologal traces from the DICOM data or from individual logfiles

    :param fn:              Physiological data from DICOM or the basename of the physiological logfiles
    :param logdatatype:     Datatype that is extracted, e.g. 'ECG', 'RESP', 'PULS' or 'EXT'. Additional meta data is extracted if 'ACQUISITION_INFO'
    :param firsttime:
    :param expectedsamples:
    :return:                traces, UUID[, nrslices, nrvolumes, firsttime, lasttime, nrechoes] ([..] if logdatatype=='ACQUISITION_INFO')
    """

    # Echoes parameter was not added until R015a, so prefill a default value for compatibility with older data
    nrechoes = 1
    traces   = None

    # Parse the input data into a list of lines
    if isinstance(fn, bytes):
        # If fn is a bytestring, we read it directly from DICOM
        lines = fn.decode('UTF-8').splitlines()
    elif isinstance(fn, Path):
        # Otherwise, fn must be a filename
        LOGGER.info(f"Reading: {fn}")
        with fn.open('r') as fid:
            lines = fid.read().splitlines()
    else:
        LOGGER.error(f"Wrong input {fn}: {type(fn)}"); raise

    # Extract the meta data and physiological traces
    LOGGER.info(f"Parsing {logdatatype} data...")
    for line in [line for line in lines if line]:

        # Strip any leading and trailing whitespace and comments
        line = line.split('#')[0].strip()

        if '=' in line:

            # This is an assigned value; parse it
            varname, value = [item.strip() for item in line.split('=')]

            if varname == 'UUID':
                UUID = value
            if varname == 'LogVersion':
                if value != expectedversion:
                    LOGGER.error(f"File format [{value}] not supported by this function (expected [{expectedversion}])"); raise
            if varname == 'LogDataType':
                if value != logdatatype:
                    LOGGER.error(f"Expected [{logdatatype}] data, found [{value}]? Check filenames?"); raise
            if varname == 'SampleTime':
                if logdatatype =='ACQUISITION_INFO':
                    LOGGER.error(f"Invalid [{varname}] parameter found"); raise
                sampletime = int(value)
            if varname == 'NumSlices':
                if logdatatype !='ACQUISITION_INFO':
                    LOGGER.error(f"Invalid [{varname}] parameter found"); raise
                nrslices = int(value)
            if varname == 'NumVolumes':
                if logdatatype !='ACQUISITION_INFO':
                    LOGGER.error(f"Invalid [{varname}] parameter found"); raise
                nrvolumes = int(value)
            if varname == 'FirstTime':
                if logdatatype !='ACQUISITION_INFO':
                    LOGGER.error(f"Invalid [{varname}] parameter found"); raise
                firsttime = int(value)
            if varname == 'LastTime':
                if logdatatype !='ACQUISITION_INFO':
                    LOGGER.error(f"Invalid [{varname}] parameter found"); raise
                lasttime = int(value)
            if varname == 'NumEchoes':
                if logdatatype !='ACQUISITION_INFO':
                    LOGGER.error(f"Invalid [{varname}] parameter found"); raise
                nrechoes = int(value)

        else:

            # This must be data; currently it is 3-5 columns, pad it with '0' if needed to always have 5 columns
            dataitems = line.split()
            dataitems = [dataitems[n] if n < len(dataitems) else '0' for n in range(5)]

            # If the first column isn't numeric, it is probably the header
            if not dataitems[0].isdigit():
                continue

            # Store data in output array based on the file type
            if logdatatype =='ACQUISITION_INFO':

                if ('nrvolumes' not in locals() or nrvolumes < 1 or
                    'nrslices'  not in locals() or nrslices  < 1 or
                    'nrechoes'  not in locals() or nrechoes  < 1):
                    LOGGER.error('Failed reading ACQINFO header'); raise
                if nrvolumes == 1:
                    # This is probably R016a or earlier diffusion data, where NumVolumes is 1 (incorrect)
                    nrvolumes = (len(lines) - 11) / (nrslices * nrechoes)
                    LOGGER.warning(f"Found NumVolumes = 1; correcting to {nrvolumes} for R016a and earlier diffusion data")
                if traces is None:
                    traces = np.zeros((2, nrvolumes, nrslices, nrechoes), dtype=int)
                curvol    = int(dataitems[0])
                curslc    = int(dataitems[1])
                curstart  = int(dataitems[2])   # TODO: check zero/one based indexing
                curfinish = int(dataitems[3])   # TODO: check zero/one based indexing
                if len(dataitems[4]):
                    cureco = int(dataitems[4])
                    if traces[:, curvol, curslc, cureco].any():
                        LOGGER.error(f"Received duplicate timing data for vol{curvol} slc{curslc} eco{cureco}"); raise
                else:
                    cureco = 0
                    if traces[:, curvol, curslc, cureco]:
                        LOGGER.warning(f"Received duplicate timing data for vol{curvol} slc{curslc} (ignore for pre-R015a multi-echo data)")
                traces[:, curvol, curslc, cureco] = [curstart, curfinish]

            else:

                curstart   = int(dataitems[0]) - firsttime
                curchannel = dataitems[1]
                curvalue   = int(dataitems[2])

                if logdatatype == 'ECG':
                    if traces is None:
                        traces = np.zeros((expectedsamples, 4), dtype=int)
                    if curchannel not in ['ECG1', 'ECG2', 'ECG3', 'ECG4']:
                        LOGGER.error(f"Invalid ECG channel ID [{curchannel}]"); raise
                    chaidx = ['ECG1', 'ECG2', 'ECG3', 'ECG4'].index(curchannel)
                elif logdatatype == 'EXT':
                    if traces is None:
                        traces = np.zeros((expectedsamples, 2), dtype=int)
                    if curchannel not in ['EXT', 'EXT2']:
                        LOGGER.error(f"Invalid EXT channel ID [{curchannel}]"); raise
                    chaidx = ['EXT', 'EXT2'].index(curchannel)
                else:
                    if traces is None:
                        traces = np.zeros((expectedsamples, 1), dtype=int)
                    chaidx = 0

                traces[curstart:curstart+int(sampletime), chaidx] = curvalue * np.ones((sampletime), dtype=int)

    if logdatatype == 'ACQUISITION_INFO':
        traces = traces - firsttime
        return traces, UUID, nrslices, nrvolumes, firsttime, lasttime, nrechoes
    else:
        return traces, UUID


def plotphysio(physio:dict, actualsamples: int):
    """Plot the samples of the physiological traces in a rudimentary way. If too large, only plot the middle 1k ticks or so"""
    displaymax = 1000
    miny       = 5E4        # Actual range is 0..4095
    maxy       = -5E4
    starttick  = 1
    endtick    = actualsamples
    if actualsamples > displaymax:
        starttick = int(actualsamples / 2) - int(displaymax / 2)
        endtick   = starttick + displaymax

    def plot_trace(logdatatype, color, scale):
        """Plot the trace and update minimum and maximum values"""
        if logdatatype not in physio: return
        nonlocal miny, maxy
        trace    = physio[logdatatype][starttick:endtick]
        mintrace = int(min(trace))      # type(ACQ)==bool
        maxtrace = int(max(trace))
        newminy  = min(miny, mintrace)
        newmaxy  = max(maxy, maxtrace)
        if scale and (newminy != mintrace or newmaxy != maxtrace):
            trace = trace * (newmaxy - newminy)/(maxtrace - mintrace) - mintrace + newminy
        plt.plot(trace, color=color, label=logdatatype)
        miny, maxy = newminy, newmaxy

    plot_trace('ECG1', 'green', False)
    plot_trace('ECG2', 'green', False)
    plot_trace('ECG3', 'green', False)
    plot_trace('ECG4', 'green', False)
    plot_trace('RESP', 'blue',  False)
    plot_trace('PULS', 'red',   False)
    plot_trace('EXT',  'cyan',  True)
    plot_trace('EXT2', 'olive', True)
    plot_trace('ACQ',  'gray',  True)

    plt.legend(loc='lower right')
    plt.axis([1, min(displaymax, actualsamples), miny - maxy*0.05, maxy + maxy*0.05])
    plt.xlabel('Samples')
    plt.show()


def readphysio(fn: Union[str,Path], showplot: bool=0) -> dict:
    """
    Read and plots active (i.e. non-zero) signals from SIEMENS advanced physiological log / DICOM files (>=R013, >=VD13A)
    E. Auerbach, CMRR, 2015-9

    This function expects to find either a combination of individual logfiles (*_ECG.log, *_RESP.log, *_PULS.log, *_EXT.log,
    *_Info.log) generated by >=R013 sequences, or a single encoded "_PHYSIO" DICOM file generated by >=R015 sequences

    Returns active (i.e. non-zero) physio traces for ECG1, ECG2, ECG3, ECG4, RESP, PULS, EXT and EXT2 signals:
    physio['UUID']:     unique identifier string for this measurement
    physio['SliceMap']: [2 x Volumes x Slices]  [1:2,:,:] = start & finish time stamp of each volume/slice
    physio['ACQ']:      [total scan time x 1]   True if acquisition is active at this time; False if not
    physio['ECG1']:     [total scan time x 1]   ECG signal on this channel
    physio['ECG2']:     [total scan time x 1]   [..]
    physio['ECG3']:     [total scan time x 1]   [..]
    physio['ECG4']:     [total scan time x 1]   [..]
    physio['RESP']:     [total scan time x 1]   RESP signal on this channel
    physio['PULS']:     [total scan time x 1]   PULS signal on this channel
    physio['EXT']:      [total scan time x 1]   True if EXT signal detected; False if not
    physio['EXT2']:     [total scan time x 1]   True if EXT2 signal detected; False if not

    The unit of time is clock ticks (2.5 ms per tick).

    :param fn:          Either the fullpath of the DICOM file or the basename of the PHYSIO logfiles (fullpath without suffix and file extension, e.g. 'foo/bar/Physio_DATE_TIME_UUID')
    :param showplot:    Plots the physiological traces if True
    :return:            The active (non-zero) physio traces for ECG1, ECG2, ECG3, ECG4, RESP, PULS, EXT, and EXT2 signals
    """

    foundECG  = False
    foundRESP = False
    foundPULS = False
    foundEXT  = False

    # Check input
    fn = Path(fn).resolve()

    # First, check if the base is pointing to a DICOM we should extract
    if fn.is_file():
        LOGGER.info(f"Attempting to read physio DICOM format file from: {fn}")
        manufacturer = bids.get_dicomfield('Manufacturer', fn)  # Performs checks
        if manufacturer != 'SIEMENS':
            LOGGER.warning(f"Unsupported manufacturer: {manufacturer}, this function is designed for SIEMENS advanced physiological logging data")
        dicomdata = dcmread(fn, force=True)                     # The DICM tag may be missing for anonymized DICOM files
        physiotag = tag.Tag('7fe1', '1010')
        if dicomdata.get('ImageType')==['ORIGINAL','PRIMARY','RAWDATA','PHYSIO'] and dicomdata.get(physiotag).private_creator=='SIEMENS CSA NON-IMAGE':
            physiodata = dicomdata[physiotag].value
            rows       = int(dicomdata.AcquisitionNumber)
            columns    = len(physiodata)/rows
            nrfiles    = columns/1024
            if columns%1 or nrfiles%1:
                LOGGER.error(f"Invalid image size: [rows x columns] = [{rows} x {columns}]"); raise
            # Encoded DICOM format: columns = 1024*nrfiles
            #                       first row: uint32 datalen, uint32 filenamelen, char[filenamelen] filename
            #                       remaining rows: char[datalen] data
            for idx in range(int(nrfiles)):
                filedata    = physiodata[idx*rows*1024:(idx+1)*rows*1024]
                datalen     = struct.unpack('<L', filedata[0:4])[0]
                filenamelen = struct.unpack('<L', filedata[4:8])[0]
                filename    = filedata[8:8+filenamelen].decode('UTF-8')
                logdata     = filedata[1024:1024+datalen]
                LOGGER.info(f"Decoded: {filename}")
                if filename.endswith('_Info.log'):
                    fnINFO    = logdata
                elif filename.endswith('_ECG.log'):
                    fnECG     = logdata
                    foundECG  = True
                elif filename.endswith('_RESP.log'):
                    fnRESP    = logdata
                    foundRESP = True
                elif filename.endswith('_PULS.log'):
                    fnPULS    = logdata
                    foundPULS = True
                elif filename.endswith('_EXT.log'):
                    fnEXT     = logdata
                    foundEXT  = True
        else:
            LOGGER.error(f"{fn} is not a valid DICOM format file"); raise

    # If we don't have an encoded DICOM, check what text log files we have
    else:
        fnINFO = fn.with_name(fn.name + '_Info.log')
        fnECG  = fn.with_name(fn.name + '_ECG.log')
        fnRESP = fn.with_name(fn.name + '_RESP.log')
        fnPULS = fn.with_name(fn.name + '_PULS.log')
        fnEXT  = fn.with_name(fn.name + '_EXT.log')
        if not fnINFO.is_file():
            LOGGER.error(f"{fnINFO} not found"); raise
        foundECG  = fnECG.is_file()
        foundRESP = fnRESP.is_file()
        foundPULS = fnPULS.is_file()
        foundEXT  = fnEXT.is_file()

    if not foundECG and not foundRESP and not foundPULS and not foundEXT:
        LOGGER.error('No data files (ECG/RESP/PULS/EXT) found'); raise

    # Read in and / or parse the data
    slicemap, UUID1, nrslices, nrvolumes, firsttime, lasttime, nrechoes = readparsefile(fnINFO, 'ACQUISITION_INFO', 0, 0)
    if lasttime <= firsttime:
        LOGGER.error(f"Last timestamp {lasttime} is not greater than first timestamp {firsttime}, aborting..."); raise
    actualsamples   = lasttime - firsttime + 1  # TODO: check zero/one indexing
    expectedsamples = actualsamples + 8         # Some padding at the end for worst case EXT sample at last timestamp

    if foundECG:
        ECG, UUID2 = readparsefile(fnECG, 'ECG', firsttime, expectedsamples)
        if UUID1 != UUID2:
            LOGGER.error('UUID mismatch between Info and ECG files'); raise

    if foundRESP:
        RESP, UUID3 = readparsefile(fnRESP, 'RESP', firsttime, expectedsamples)
        if UUID1 != UUID3:
            LOGGER.error('UUID mismatch between Info and RESP files'); raise

    if foundPULS:
        PULS, UUID4 = readparsefile(fnPULS, 'PULS', firsttime, expectedsamples)
        if UUID1 != UUID4:
            LOGGER.error('UUID mismatch between Info and PULS files'); raise

    if foundEXT:
        EXT, UUID5 = readparsefile(fnEXT, 'EXT', firsttime, expectedsamples)
        if UUID1 != UUID5:
            LOGGER.error('UUID mismatch between Info and EXT files'); raise

    LOGGER.info(f"Slices in scan:      {nrslices}")
    LOGGER.info(f"Volumes in scan:     {nrvolumes}")
    LOGGER.info(f"Echoes per slc/vol:  {nrechoes}")
    LOGGER.info(f"First timestamp:     {firsttime}")
    LOGGER.info(f"Last timestamp:      {lasttime}")
    LOGGER.info(f"Total scan duration: {actualsamples} ticks = {actualsamples*2.5/1000:.4f} s")

    LOGGER.info('Formatting ACQ data...')
    ACQ = np.full((expectedsamples, 1), False)
    for v in range(nrvolumes):
        for s in range(nrslices):
            for e in range(nrechoes):
                ACQ[slicemap[0,v,s,e]:slicemap[1,v,s,e]+1, 0] = True        # TODO: check zero/one based indexing

    # Only return active (nonzero) physio traces
    physio             = dict()
    physio['UUID']     = UUID1
    physio['SliceMap'] = slicemap
    physio['ACQ']      = ACQ
    if foundECG and ECG.any():
        if sum(ECG[:,0]): physio['ECG1'] = ECG[:,0]
        if sum(ECG[:,1]): physio['ECG2'] = ECG[:,1]
        if sum(ECG[:,2]): physio['ECG3'] = ECG[:,2]
        if sum(ECG[:,3]): physio['ECG4'] = ECG[:,3]
    if foundRESP and RESP.any():
        if sum(RESP):     physio['RESP'] = RESP
    if foundPULS and PULS.any():
        if sum(PULS):     physio['PULS'] = PULS
    if foundEXT and EXT.any():
        if sum(EXT[:,0]): physio['EXT']  = EXT[:,0]
        if sum(EXT[:,1]): physio['EXT2'] = EXT[:,1]

    # Plot the data if requested
    if showplot:
        plotphysio(physio, actualsamples)

    return physio


def main():
    """Console script usage"""

    # Parse the input arguments and run readphysio(args)
    import argparse

    parser = argparse.ArgumentParser(formatter_class=argparse.RawDescriptionHelpFormatter,
                                     description=__doc__,
                                     epilog='examples:\n'
                                            '  readphysio /project/3022026.01/sub-001/MR000000.dcm\n'
                                            '  readphysio /project/3022026.01/sub-001/Physio_20200428_142451_007e910e-02d9-4d7a-8fdb-8e3568be8322 -s\n ')
    parser.add_argument('filename', help="Either the fullpath of the DICOM file or the basename of the PHYSIO logfiles (fullpath without suffix and file extension, e.g. 'foo/bar/Physio_DATE_TIME_UUID'")
    args = parser.parse_args()

    readphysio(fn=args.filename, showplot=True)


if __name__ == "__main__":
    main()
