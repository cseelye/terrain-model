"""Helpers for processing GIS data into 3D models"""
from __future__ import print_function
from gtm1.geotrimesh import mesh
from pyapputil.logutil import GetLogger
from pyapputil.exceptutil import ApplicationError
from pyapputil.shellutil import Shell
from util import download_file

from affine import Affine
import bisect
from dateutil.parser import isoparse
from lxml import etree as ET
import math
import os
from osgeo import gdal, osr
from pathlib import Path
import requests
from requests import HTTPError
import shutil
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
# UL = N,W or max_lat, min_long
# LR = S,E or min_lat, max_long
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

def convert_and_crop_raster(input_filename, output_filename, min_lat, min_long, max_lat, max_long, output_type="GTiff", remove_alpha=False):
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

    center_lat =  (max_lat + min_lat)/2
    real_width = (max_long - min_long) * degree_long_to_miles(center_lat)
    real_height = (max_lat - min_lat) * degree_lat_to_miles(center_lat)
    log.debug("Cropping to {}mi wide by {}mi high".format(real_width, real_height))

    # Using shell commands for this because the native python interface isn't working after recent GDAL upgrade plus testing agaisnt more types of input files
    # Use shell commands until I have time to debug/fix the native code
    from pyapputil.shellutil import Shell
    log.info("Cropping to boundaries top(max_lat)={} left(min_long)={} bottom(min_lat)={} right(max_long)={}".format(max_lat, min_long, min_lat, max_long))
    log.info("Converting to {}".format(output_type))
    band_args = "-b 1 -b 2 -b 3" if remove_alpha else ""
    retcode, _, stderr = Shell("gdal_translate -of {output_type} {band_args} -projwin_srs EPSG:4326 -projwin {min_long} {max_lat} {max_long} {min_lat} {infile} {outfile}".format(
        output_type=output_type,
        band_args=band_args,
        max_lat=max_lat,
        min_lat=min_lat,
        max_long=max_long,
        min_long=min_long,
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

def dem_to_model2(dem_filename, model_filename, z_exaggeration=1.0):
    """
    """


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
    min_long, max_lat, _ = trans.TransformPoint(source_extent[0], source_extent[3], 0.0)
    max_long, min_lat, _ = trans.TransformPoint(source_extent[2], source_extent[1], 0.0)
    return (min_lat, min_long, max_lat, max_long)

def get_elevation_tilename(latitude, longitude):
    """
    Get the filename for a tile covering the given coordinates.
    """
    upper = int(math.ceil(latitude))
    left = int(math.floor(longitude))
    return "dem-{}.tif".format(get_coords_string(upper, left))

def get_image_tile_name(latitude, longitude):
    """
    Get the filename for an image tile covering the given coordinates.
    """
    return "image-{}.tif".format(get_coords_string(latitude, longitude))

def get_cropped_elevation_filename(max_lat, min_long, min_lat, max_long):
    return "cropped-dem-{coords_ul}_{coords_lr}.tif".format(
                    coords_ul=get_coords_string(max_lat, min_long),
                    coords_lr=get_coords_string(min_lat, max_long))

def get_cropped_image_filename(max_lat, min_long, min_lat, max_long):
    return "cropped-image-{coords_ul}_{coords_lr}.tif".format(
                    coords_ul=get_coords_string(max_lat, min_long),
                    coords_lr=get_coords_string(min_lat, max_long))

def get_coords_string(latitude, longitude):
    return "{}{}{}{}".format("n" if latitude > 0 else "s",
                                 abs(latitude),
                                 "e" if longitude > 0 else "w",
                                 abs(longitude))

def download_elevation_tile(latitude, longitude, dest_dir):
    """
    Download elevation data for a given lat/long region. This will download the
    1x1 degree tile covering the requested area, rename it to a standard
    filename, convert it to a GeoTiff, and store it in the destination dir. If
    the file already exists in the destination no action will be taken.

    Args:
        latitude:   latitude of a point in the region
        longitude:  longitude of a point in the region
        dest_dir:   the directory to download the tile to

    Returns:
        The full path of the elevation file (Path)
    """
    log = GetLogger()

    upper = int(math.ceil(latitude))
    left = int(math.floor(longitude))

    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)

    # Check the local cache to see if we already have this tile
    output_file = dest_dir / Path(get_elevation_tilename(latitude, longitude))
    log.debug("Checking cache for {}".format(output_file))
    if output_file.exists():
        log.info("Using cached elevation data for ({},{})".format(upper, left))
        return output_file

    # Query the National Map API for elevation products
    log.info("Downloading elevation data for ({},{})".format(upper, left))
    query_url = "https://tnmaccess.nationalmap.gov/api/v1/products"
    payload = {
        "bbox": "{},{},{},{}".format(left, upper, left, upper),
        "datasets": "National Elevation Dataset (NED) 1/3 arc-second",
        "max": 10
    }
    with requests.get(query_url, params=payload) as req:
        log.debug(req.url)
        req.raise_for_status()
        resp = req.json()
    # Filter to only matching lat/long
    items = [x for x in resp["items"] if round(x["boundingBox"]["minX"], 1) == left and round(x["boundingBox"]["maxY"], 1) == upper]
    # Sort by date to get the newest
    items.sort(key=lambda x: isoparse(x["dateCreated"]))
    if len(items) <= 0:
        raise ApplicationError("Could not find an elevation product for the requested area from the National Map")
    url = items[0]["downloadURL"]

    # Download the file we got from the API
    with tempfile.TemporaryDirectory() as download_dir:
        pieces = urlparse(url)
        base_filename = Path(pieces.path).name
        local_filename = Path(download_dir) / base_filename
        download_file(url, local_filename)

        # Extract if necessary
        if local_filename.suffix == ".zip":
            log.info("Extracting elevation data file")
            log.debug("Extracting {} to {}".format(local_filename, download_dir))
            archive = zipfile.ZipFile(local_filename)
            archive.extractall(path=download_dir)
            # Find the data file in the zip
            coords = "{}{}{}{}".format("n" if upper > 0 else "s",
                                    abs(upper),
                                    "e" if left > 0 else "w",
                                    abs(left))
            known_filenames = ["grd{}_13".format(coords), "USGS_NED_13_{}_IMG.img".format(coords), "img{}_13.img".format(coords)]
            for fname in known_filenames:
                if (download_dir / fname).exists():
                    data_filename = download_dir / fname
                    break
        else:
            data_filename = local_filename

        # Convert to GeoTIFF
        retcode, _, stderr = Shell("gdal_translate -of {output_type} {infile} {outfile}".format(
            output_type="GTiff",
            infile=data_filename,
            outfile=output_file
        ))
        if retcode != 0 or "ERROR" in stderr:
            raise ApplicationError("Could not convert tile: {}".format(stderr))

        return output_file

def download_image_tile(latitude, longitude, dest_dir):
    """
    Download image file for a given lat/long region. This will download the
    3.75' x 3.75' tile covering the requested area, rename it to a standard
    filename, and store it in the destination dir. If the file already
    exists in the destination no action will be taken.

    Args:
        latitude:   latitude of the upper left corner of the region
        longitude:  longitude of the upper left corner of the region
        dest_dir:   the directory to download the tile to

    Returns:
        The full path of the image file (Path)
    """
    log = GetLogger()

    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)

    # Check the local cache to see if we already have this tile
    output_file = dest_dir / Path(get_image_tile_name(latitude, longitude))
    log.debug("Checking cache for {}".format(output_file))
    if output_file.exists():
        log.info("Using cached elevation data for ({},{})".format(latitude, longitude))
        return output_file

    # Query the National Map API for image products
    log.info("Downloading image data for ({},{})".format(latitude, longitude))
    query_url = "https://tnmaccess.nationalmap.gov/api/v1/products"
    payload = {
        "bbox": "{},{},{},{}".format(longitude, latitude, longitude, latitude),
        "datasets": "USDA National Agriculture Imagery Program (NAIP)",
        "max": 100
    }
    with requests.get(query_url, params=payload) as req:
        log.debug(req.url)
        req.raise_for_status()
        resp = req.json()
    # Filter to only exact matching lat/long
    items = [x for x in resp["items"] if round(x["boundingBox"]["minX"], 4) == longitude and round(x["boundingBox"]["maxY"], 4) == latitude]
    # Sort by date to get the newest
    items.sort(key=lambda x: isoparse(x["dateCreated"]))
    if len(items) <= 0:
        raise ApplicationError("Could not find an image product for the requested area from the National Map")

    # Download the file we got from the API
    url = items[0]["downloadURL"]
    with tempfile.TemporaryDirectory() as download_dir:
        pieces = urlparse(url)
        base_filename = Path(pieces.path).name
        local_filename = Path(download_dir) / base_filename
        download_file(url, local_filename)

        # Move the downloaded file to the final location
        shutil.move(local_filename, output_file)

        return output_file

def get_image_tile_interval():
    # USGS distributes elevation data in 3.75' tiles, 3.75' = 0.0625 degree
    return 0.0625

def get_elevation_tile_interval():
    # USGS distributes elevation data in 1 degree tiles
    return 1

def get_image_tile_coords(latitude, longitude):
    """
    Get the coordinates of the upper left corner of the image tile containing a point
    """
    return get_tile_coords_range(latitude, latitude, longitude, longitude, get_image_tile_interval())[0]

def get_image_tile_range(min_lat, min_long, max_lat, max_long):
    return get_tile_coords_range(min_lat, min_long, max_lat, max_long, get_image_tile_interval())

def get_elevation_tile_coords(latitude, longitude):
    """
    Get the coordinates of the upper left corner of the elevation tile containing a point
    """
    return get_tile_coords_range(latitude, latitude, longitude, longitude, get_elevation_tile_interval())[0]

def get_elevation_tile_range(min_lat, min_long, max_lat, max_long):
    return get_tile_coords_range(min_lat, min_long, max_lat, max_long, get_elevation_tile_interval())

def get_tile_coords_range(min_lat, min_long, max_lat, max_long, interval):
    """
    Get the coordinates of the upper left corner of the image tile containing a point
    """
    upper = int(math.ceil(abs(max_lat)))
    if max_lat < 0:
        upper = -upper
    lower = int(math.ceil(abs(min_lat)))
    if min_lat < 0:
        lower = -lower
    left = int(math.ceil(abs(min_long)))
    if min_long < 0:
        left = -left
    right = int(math.ceil(abs(max_long)))
    if max_long < 0:
        right = -right

    lat_intervals = [(lower - 1) + (x * interval) for x in range(0, int(((upper + 1) - (lower - 1)) / interval) + 1)]
    long_intervals = [(left - 1) + (x * interval) for x in range(0, int(((right + 1) - (left - 1)) / interval) + 1)]

    # Find the index of the tiles for latitude
    max_lat_tile_idx = bisect.bisect_left(lat_intervals, max_lat)
    min_lat_tile_idx = bisect.bisect_left(lat_intervals, min_lat)


    # Find the index of the tiles for longitude
    max_long_tile_idx = bisect.bisect_right(long_intervals, max_long) - 1
    min_long_tile_idx = bisect.bisect_right(long_intervals, min_long) - 1

    tiles = []
    for lat in lat_intervals[min_lat_tile_idx:max_lat_tile_idx+1]:
        for long in long_intervals[min_long_tile_idx:max_long_tile_idx+1]:
            tiles.append((lat, long))
    return tiles

def get_dem_data(dem_filename, min_lat, min_long, max_lat, max_long, cache_dir=Path("cache")):
    """
    Get the elevation data for the given region. This will download, crop and convert the requested elevation data

    Args:
        dem_filename:   file to save the elevation data to (Path)
        min_lat:        south border of the region (float)
        min_long:       east border of the region (float)
        max_lat:        north border of the region (float)
        max_long:       west border of the region (float)
        cache_dir:      the path to save/look for the elevation data (Path)
    """
    log = GetLogger()

    log.debug("Checking cache for {}".format(cache_dir / dem_filename))
    if (cache_dir / dem_filename).exists():
        log.info("Using cached elevation data file {}".format(dem_filename))
        return

    # Make a list of elevation tiles we need to cover this region and download them
    tile_coords = get_elevation_tile_range(min_lat, min_long, max_lat, max_long)
    elevation_files = []
    for lat, long in tile_coords:
        tile_file = download_elevation_tile(lat, long, cache_dir)
        elevation_files.append(tile_file)

    # Create a virtual data source with all of the tiles
    log.debug("Creating virtual data set with tiles {}".format(",".join(str(f) for f in elevation_files)))
    _, crop_input_file = tempfile.mkstemp()
    retcode, _, stderr = Shell("gdalbuildvrt {} {}".format(crop_input_file, " ".join(str(f) for f in elevation_files)))
    if retcode != 0 or "ERROR" in stderr:
        raise ApplicationError("Could not merge input files: {}".format(stderr))

    # Crop to the requested region and convert to geotiff
    log.info("Converting and cropping elevation data")
    convert_and_crop_raster(crop_input_file, cache_dir / dem_filename, min_lat, min_long, max_lat, max_long, remove_alpha=False)

def get_image_data(image_filename, min_lat, min_long, max_lat, max_long, cache_dir="cache"):
    """
    Get the imagery data for the given region. This will download, crop and convert the requested data

    Args:
        image_filename:     file to save the image to (Path)
        min_lat:            south border of the region (float)
        min_long:           east border of the region (float)
        max_lat:            north border of the region (float)
        max_long:           west border of the region (float)
    """
    log = GetLogger()

    if (cache_dir / image_filename).exists():
        log.info("Using cached image file {}".format(image_filename))
        return

    # Make a list of image tiles we need to cover this region and download them
    tile_coords = get_image_tile_range(min_lat, min_long, max_lat, max_long)
    image_files = []
    for lat, long in tile_coords:
        tile_file = download_image_tile(lat, long, cache_dir)
        image_files.append(tile_file)

    # Create a virtual data source with all of the tiles
    log.debug("Creating virtual data set with tiles {}".format(",".join(str(f) for f in image_files)))
    _, crop_input_file = tempfile.mkstemp()
    retcode, _, stderr = Shell("gdalbuildvrt {} {}".format(crop_input_file, " ".join(str(f) for f in image_files)))
    if retcode != 0 or "ERROR" in stderr:
        raise ApplicationError("Could not merge input files: {}".format(stderr))

    # Crop to the requested region and convert to geotiff
    log.info("Converting and cropping image data")
    convert_and_crop_raster(crop_input_file, cache_dir / image_filename, min_lat, min_long, max_lat, max_long, remove_alpha=True)
