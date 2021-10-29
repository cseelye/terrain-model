"""Helpers for processing GIS data into 3D models"""
from __future__ import print_function
from gtm.geotrimesh import mesh
from pyapputil.logutil import GetLogger
from pyapputil.exceptutil import ApplicationError
from util import download_file

from affine import Affine
from lxml import etree as ET
import math
import os
from osgeo import gdal, osr
from pathlib import Path
from requests import HTTPError
import tempfile
try:
    from urllib.parse import urlparse
except ImportError:
    from urlparse import urlparse
import zipfile

# Make GDAL throw exceptions for Failure/Fatal messages
gdal.UseExceptions()

class GdalObjectNoCheck(object):
    pass

class GdalErrorHandler(object):
    def __init__(self):
        self.err_level = gdal.CE_None
        self.err_no = 0
        self.err_msg = ""
        self.log = GetLogger()

    def handler(self, err_level, err_no, err_msg):
        self.err_level = err_level
        self.err_no = err_no
        self.err_msg = err_msg

    def check(self, extra_msg=None, gdal_obj=GdalObjectNoCheck()):
        try:
            if not self.is_set():
                return

            app_msg = "{}: ".format(extra_msg) or ""

            # First check if we have an error set and raise/log if so
            if self.is_error():
                raise ApplicationError("{}GDAL error {}: {}".format(app_msg, self.err_no, self.err_msg))
            if self.is_warning():
                self.log.warning("GDAL warning {}: {}".format(self.err_no, self.err_msg))

            # Sometimes GDAL deos not call the error handler even when there is an error
            # Check of the gdal object passed in is null
            if gdal_obj is None:
                # See if GDAL is holding an error
                error_msg = gdal.GetLastErrorMsg()
                error_no = gdal.GetLastErrorNo()
                error_level = gdal.GetLastErrorType()
                if error_level and error_level > gdal.CE_None:
                    raise ApplicationError("{}GDAL error - possible message {}: {}".format(app_msg, error_no, error_msg))
                else:
                    raise ApplicationError("{}GDAL error")
        finally:
            self.reset()

    def reset(self):
        self.err_level = gdal.CE_None
        self.err_no = 0
        self.err_msg = ""

    def is_set(self):
        return self.err_level and self.err_level > gdal.CE_None
    def is_warning(self):
        return self.err_level == gdal.CE_Warning
    def is_error(self):
        return self.err_level > gdal.CE_Warning

GDAL_ERROR = GdalErrorHandler()
gdal_error_handler_func = GDAL_ERROR.handler
gdal.PushErrorHandler(gdal_error_handler_func)

# Send GDAL logging that would normally go to stderr to our python logger instead
MYLOG = GetLogger()
gdal.ConfigurePythonLogging(logger_name=MYLOG.name)


# Notes on Axis order
# Latitude:   -90s to 90n degrees, north positive, south negative, Y axis
# Longtitude: -180w to 180e degrees, east positive, west negative, X axis
# https://github.com/OSGeo/gdal/blob/master/MIGRATION_GUIDE.TXT - see "MIGRATION GUIDE FROM GDAL 2.4 to GDAL 3.0"
# https://gis.stackexchange.com/a/364947
# https://gis.stackexchange.com/a/99862
# sr = osr.SpatialReference()
# sr.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)

class WGS84(object):
    """Constants for WGS84 spheroid calculations"""
    m1 = 111132.9526
    m2 = -559.84957
    m3 = 1.17514
    m4 = -0.0023
    p1 = 111412.8773
    p2 = -93.50412
    p3 = 0.11774
    p4 = -0.000165

class GPXFile(object):
    """Abstract a GPX file and common operations on it"""

    def __init__(self, filename):
        """
        Args:
            filename:   the name of the GPX file (str)
        """
        self.filename = filename

    def GetBounds(self, padding=0, square=False):
        """
        Get the min and max latitude and longitude values in the tracks in the GPX file

        Args:
            padding:    additional padding to add around the bounds, in miles (float)
            square:     square the region around the tracks instead of following them exactly (bool)

        Returns:
            A tuple of floats (min_lat, min_long, max_lat, max_long)
        """
        log = GetLogger()
        tree = ET.parse(self.filename)
        root = tree.getroot()
        max_lat = -90.0
        min_lat = 90.0
        max_long = -180.0
        min_long = 180.0
        for node in root.findall("trk/trkseg/trkpt", root.nsmap):
            node_lat = float(node.attrib["lat"])
            node_long = float(node.attrib["lon"])

            if node_lat > max_lat:
                max_lat = node_lat
            if node_lat < min_lat:
                min_lat = node_lat
            if node_long > max_long:
                max_long = node_long
            if node_long < min_long:
                min_long = node_long
        center_lat, center_long = ( (max_lat + min_lat)/2, (max_long + min_long)/2 )
        log.debug2("GPX tracks min_lat={}, min_long={}, max_lat={}, max_long={}, center_lat={}, center_long={}".format(min_lat, min_long, max_lat, max_long, center_lat, center_long))

        if square:
            width = max_long - min_long
            height = max_lat - min_lat
            size = max(width, height)
            log.debug("width={}, height={}, size={}".format(width, height, size))
            min_lat = center_lat - size/2
            max_lat = center_lat + size/2
            min_long = center_long - size/2
            max_long = center_long + size/2
            log.debug2("Squared min_lat={}, min_long={}, max_lat={}, max_long={}".format(min_lat, min_long, max_lat, max_long))

        if padding != 0:
            min_lat -= padding / degree_lat_to_miles(center_lat)
            min_long -= padding / degree_long_to_miles(center_lat)
            max_lat += padding / degree_lat_to_miles(center_lat)
            max_long += padding / degree_long_to_miles(center_lat)
            log.debug2("Padded min_lat={}, min_long={}, max_lat={}, max_long={}".format(min_lat, min_long, max_lat, max_long))

        return (min_lat, min_long, max_lat, max_long)

    def GetCenter(self):
        """
        Get the coordinates of the center of the tracks in the GPX file

        Returns:
            A tuple of floats (center_lat, center_long)
        """
        min_lat, min_long, max_lat, max_long = self.GetBounds()
        return ( (max_lat - min_lat)/2, (max_long - min_long)/2 )

    def GetTrackPoints(self):
        tracks = []
        tree = ET.parse(self.filename)
        root = tree.getroot()
        for track_node in root.findall("trk", root.nsmap):
            track = []
            for node in track_node.findall("trkseg/trkpt", root.nsmap):
                node_lat = float(node.attrib["lat"])
                node_long = float(node.attrib["lon"])
                track.append((node_lat, node_long))
            tracks.append(track)
        return tracks

    def ToCSV(self, csvfile):
        tree = ET.parse(self.filename)
        root = tree.getroot()
        with open(csvfile, "w") as outfile:
            outfile.write("LON,LAT\n")
            for node in root.findall("trk/trkseg/trkpt", root.nsmap):
                node_lat = float(node.attrib["lat"])
                node_long = float(node.attrib["lon"])
                outfile.write("{},{}\n".format(node_long, node_lat))


def degree_long_to_miles(lat):
    """
    Calculate the length of 1 degree of longitude in miles at a given latitude

    Args:
        lat:    latitude to measure at, in decimal degrees (float)

    Returns:
        The length in miles (float)
    """
    rads = lat * 2 * math.pi / 360
    return (WGS84.p1 * math.cos(rads) + WGS84.p2 * math.cos(3*rads) + WGS84.p3 * math.cos(5*rads)) * 0.000621371

def degree_lat_to_miles(lat):
    """
    Calculate the length of 1 degree of latitude at a given latitude

    Args:
        lat:    latitude to measure at, in decimal degrees (float)

    Returns:
        The length in miles (float)
    """
    rads = lat * 2 * math.pi / 360
    return (WGS84.m1 + WGS84.m2 * math.cos(2*rads) + WGS84.m3 * math.cos(4*rads) + WGS84.m4 * math.cos(6*rads)) * 0.000621371

def convert_and_crop_raster(input_filename, output_filename, min_lat, min_long, max_lat, max_long, output_type="GTiff", remove_alpha=True):
    """
    Convert a georeferenced raster file to another format and crop it to the given GPS coordinates

    Args:
        input_filename:     the name of the file to convert (string or Path)
        output_filename:    the name of the new file to create (string)
        min_lat:            the south edge of the new file, in decimal degrees (float)
        min_long:           the west edge of the new file, in decimal degrees (float)
        max_lat:            the north edge of the new file, in decimal degrees (float)
        max_long:           the east edge of the new file, in decimal degrees (float)
        output_type:        the data format of the new file (string)
        remove_alpha:       remove the transparency from the new file, only for images (bool)
    """
    log = GetLogger()

    # Using shell commands for this because the native python interface isn't working after recent GDAL upgrade plus testing agaisnt more types of input files
    # Use shell commands until I have time to debug/fix the native code
    from pyapputil.shellutil import Shell
    log.info("Cropping to boundaries top(max_lat)={} left(min_long)={} bottom(min_lat)={} right(max_long)={}".format(max_lat, min_long, min_lat, max_long))
    log.info("Converting to {}".format(output_type))
    band_args = "-b 1 -b 2 -b 3" if remove_alpha else ""
    retcode, _, stderr = Shell("gdal_translate -of {output_type} {band_args} -projwin_srs EPSG:4326 -projwin -116.0765 38.39 -116.061 38.378 {infile} {outfile}".format(
        output_type=output_type,
        band_args=band_args,
        infile=input_filename,
        outfile=output_filename
    ))
    if retcode != 0 or "ERROR" in stderr:
        raise ApplicationError("Could not crop file: {}".format(stderr))

    # ds = gdal.Open(str(input_filename))
    # GDAL_ERROR.check("Error parsing input file", ds)

    # log.debug("Input type: %s", ds.GetDriver().LongName)
    # log.debug("Input files: %s", ds.GetFileList())
    # log.debug("Input raster size: %s x %s", ds.RasterXSize, ds.RasterYSize)

    # with tempfile.NamedTemporaryFile() as tf:
    #     input_srs = osr.SpatialReference()
    #     input_srs.ImportFromWkt(ds.GetProjection())
    #     log.debug("Input SR: %s", input_srs.GetName())
    #     output_srs = osr.SpatialReference()
    #     output_srs.ImportFromEPSG(4326) # WGS84
    #     output_srs.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    #     log.debug("Output SR: %s", output_srs.GetName())
    #     log.info("Cropping to boundaries top(max_lat)={} left(min_long)={} bottom(min_lat)={} right(max_long)={}".format(max_lat, min_long, min_lat, max_long))
    #     ret = gdal.Warp(tf.name, ds, outputBounds=(min_long, min_lat, max_long, max_lat),
    #                                  outputBoundsSRS=output_srs,
    #                                  multithread=True,
    #                                  srcAlpha=False,
    #                                  dstAlpha=False,
    #                                  dstNodata=[255, 255, 255])
    #     GDAL_ERROR.check("Error cropping", ret)
    #     log.debug("Wrote temp ds = {}".format(tf.name))

    #     log.info("Converting to {}".format(output_type))
    #     temp_ds = gdal.Open(tf.name)
    #     GDAL_ERROR.check("Error opening temp file", temp_ds)
    #     band_list = None
    #     if remove_alpha:
    #         band_list = range(1, temp_ds.RasterCount)
    #     ret = gdal.Translate(str(output_filename), temp_ds, format=output_type, bandList=band_list)
    #     GDAL_ERROR.check("Error converting", ret)

def dem_to_model(dem_filename, model_filename, z_exaggeration=1.0):
    """
    Convert a DEM file to an x3d model file

    Args:
        dem_filename:       name of the input DEM data file (string)
        model_filename:     name of the output file to create (string)
        z_exaggeration:     multiplier to appy to the Z elevation values (float)
    """
    log = GetLogger()

    outdir = os.path.dirname(os.path.abspath(model_filename))
    outprefix = os.path.basename(model_filename).split(".")[0]

    elevation = mesh.ElevationMesh(log)
    elevation.generate_mesh(dem=str(dem_filename),
                            mesh_path=outdir,
                            mesh_prefix=outprefix,
                            z_exaggeration=z_exaggeration)

def get_raster_boundaries_geo(data_source):
    """
    Get the min and max image georeferenced coordinates of the area of the image

    Args:
        data_source:    a GDAL dataset (osgeo.gdal.Dataset)

    Returns:
        A tuple of floats (min_col, min_row, max_col, max_row)
    """
    gt = data_source.GetGeoTransform()
    GDAL_ERROR.check("Error reading geotransform", gt)
    min_col = gt[0]
    max_row = gt[3]
    # http://www.perrygeo.com/python-affine-transforms.html
    a = Affine.from_gdal(*gt)
    max_col, min_row = a * (data_source.RasterXSize, data_source.RasterYSize)
    return (min_col, min_row, max_col, max_row)

def get_raster_boundaries_gps(data_source):
    """
    Get the min and max image GPS coordinates of the area of the image

    Args:
        data_source:    a GDAL dataset (osgeo.gdal.Dataset)

    Returns:
        A tuple of floats (min_lat, min_long, max_lat, max_long)
    """
    source_extent = get_raster_boundaries_geo(data_source)
    source_srs = osr.SpatialReference()
    source_srs.ImportFromWkt(data_source.GetProjection())
    target_srs = osr.SpatialReference()
    target_srs.ImportFromEPSG(4326) # WGS84
    trans = osr.CoordinateTransformation(source_srs, target_srs)
    max_lat, min_long, _ = trans.TransformPoint(source_extent[0], source_extent[3], 0.0)
    min_lat, max_long, _ = trans.TransformPoint(source_extent[2], source_extent[1], 0.0)
    return (min_lat, min_long, max_lat, max_long)

def get_dem_data(dem_filename, min_lat, min_long, max_lat, max_long):
    """
    Get the elevation data for the given region. This will download, crop and convert the requested elevation data

    Args:
        dem_filename:   file to save the elevation data to
        min_lat:        south border of the region
        min_long:       east border of the region
        max_lat:        north border of the region
        max_long:       west border of the region
    """
    log = GetLogger()

    log.debug("local elevation data file = {}".format(dem_filename))
    if dem_filename.exists():
        log.info("Using existing elevation data file {}".format(dem_filename))
        return

    log.info("Downloading elevation data")
    workdir = Path(tempfile.mkdtemp())
    log.debug("download workdir={}".format(workdir))

    upper = int(math.ceil(max_lat))
    left = int(math.floor(max_long))

    # Find an available elevation product
    coords = "{}{}{}{}".format("n" if upper > 0 else "s",
                               abs(upper),
                               "e" if left > 0 else "w",
                               abs(left))
    coords2 = "{}{}{}{:03d}".format("n" if upper > 0 else "s",
                               abs(upper),
                               "e" if left > 0 else "w",
                               abs(left))
    possible_urls = [
        {
            "url": "https://prd-tnm.s3.amazonaws.com/StagedProducts/Elevation/13/ArcGrid/{}.zip".format(coords),
            "data_path": "grd{}_13".format(coords)
        },
        {
            "url": "https://prd-tnm.s3.amazonaws.com/StagedProducts/Elevation/13/IMG/USGS_NED_13_{}_IMG.zip".format(coords),
            "data_path": "USGS_NED_13_{}_IMG.img".format(coords)
        },
        {
            "url": "https://prd-tnm.s3.amazonaws.com/StagedProducts/Elevation/13/IMG/{}.zip".format(coords),
            "data_path": "img{}_13.img".format(coords)
        },
        {
            "url": "https://prd-tnm.s3.amazonaws.com/StagedProducts/Elevation/13/TIFF/{coords2}/USGS_13_{coords2}.tif".format(coords2=coords2),
            "data_path": "USGS_13_{}.tif".format(coords2)
        }
    ]
    found = False
    for elevation_data in possible_urls:
        url = elevation_data["url"]
        pieces = urlparse(url)
        base_filename = Path(pieces.path).name
        local_filename = workdir / base_filename
        try:
            download_file(url, local_filename)
            found = True
            break
        except HTTPError:
            continue
    if not found:
        raise ApplicationError("Could not find an elevation product for the requested area from the National Map")

    # Extract if necessary
    if local_filename.suffix == ".zip":
        log.info("Extracting elevation data file")
        log.debug("Extracting {} to {}".format(local_filename, workdir))
        archive = zipfile.ZipFile(local_filename)
        archive.extractall(path=workdir)

    log.info("Converting and cropping elevation data")
    input_data = workdir / elevation_data["data_path"]
    convert_and_crop_raster(input_data, dem_filename, min_lat, min_long, max_lat, max_long)
