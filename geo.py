"""Helpers for processing GIS data into 3D models"""
import bisect
import math
import os
from pathlib import Path
import shutil
import tempfile
from urllib.parse import urlparse
import zipfile

from affine import Affine
from dateutil.parser import isoparse
from lxml import etree as ET
from osgeo import gdal, osr
from pyapputil.logutil import GetLogger
from pyapputil.exceptutil import ApplicationError
from pyapputil.shellutil import Shell
import requests

from gtm1.geotrimesh import mesh
from gtm2.generate_terrain import generate_terrain as gtm2_generate_terrain
from util import download_file, list_like


# Make GDAL throw exceptions for Failure/Fatal messages
gdal.UseExceptions()

class GdalObjectNoCheck:
    """Dummy object to signal the GDAL error handler to not check"""

class GdalErrorHandler:
    """Handle error message callbacks and error checking after GDAL function
    calls"""
    def __init__(self):
        self.err_level = gdal.CE_None
        self.err_no = 0
        self.err_msg = ""
        self.log = GetLogger()

    def handler(self, err_level, err_no, err_msg):
        """GDAL error message callback"""
        self.err_level = err_level
        self.err_no = err_no
        self.err_msg = err_msg

    def check(self, extra_msg=None, gdal_obj=GdalObjectNoCheck()):
        """
        Check for a GDAL error after calling a GDAL function. This handles the
        odd cases where GDAL does not throw an error but returnes an empty
        result. This function will throw if there was a GDAL error.

        Args:
            extra_msg:  (string) Optional error message to add context.
            gdal_obj:   (object) Optional GDAL object to check. Pass
                                 GdalObjectNoCheck to skip the check.
        """
        try:
            if not self.is_set():
                return

            app_msg = f"{extra_msg}: " or ""

            # First check if we have an error set and raise/log if so
            if self.is_error():
                raise ApplicationError(f"{app_msg}GDAL error {self.err_no}: {self.err_msg}")
            if self.is_warning():
                self.log.warning(f"GDAL warning {self.err_no}: {self.err_msg}")

            # Sometimes GDAL deos not call the error handler even when there is an error
            # Check of the gdal object passed in is null
            if gdal_obj is None:
                # See if GDAL is holding an error
                error_msg = gdal.GetLastErrorMsg()
                error_no = gdal.GetLastErrorNo()
                error_level = gdal.GetLastErrorType()
                if error_level and error_level > gdal.CE_None:
                    raise ApplicationError(f"{app_msg}GDAL error - possible message {error_no}: {error_msg}")
                raise ApplicationError(f"{app_msg}GDAL error")
        finally:
            self.reset()

    def reset(self):
        """Reset the error so the handler can be used again"""
        self.err_level = gdal.CE_None
        self.err_no = 0
        self.err_msg = ""

    def is_set(self):
        """Check if an error is set"""
        return self.err_level and self.err_level > gdal.CE_None
    def is_warning(self):
        """Check if there was a warning"""
        return self.err_level == gdal.CE_Warning
    def is_error(self):
        """Check if there was an error"""
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

class WGS84:
    """Constants for WGS84 spheroid calculations"""
    m1 = 111132.9526
    m2 = -559.84957
    m3 = 1.17514
    m4 = -0.0023
    p1 = 111412.8773
    p2 = -93.50412
    p3 = 0.11774
    p4 = -0.000165

class GPXFile:
    """Abstract a GPX file and common operations on it"""

    def __init__(self, filename):
        """
        Args:
            filename:   (str) The name of the GPX file.
        """
        self.filename = filename

    def GetBounds(self, padding=0, square=False):
        """
        Get the min and max latitude and longitude values in the tracks in the
        GPX file.

        Args:
            padding:    (float) Additional padding to add around the bounds, in
                                miles.
            square:     (bool)  Square the region around the tracks instead of
                                following them exactly.

        Returns:
            (tuple of float) Bounding box coordinates min_lat, min_long,
            max_lat, max_long.
        """
        log = GetLogger()
        root = self._parse()
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
        log.debug2(f"GPX tracks min_lat={min_lat}, min_long={min_long}, max_lat={max_lat}, max_long={max_long}, center_lat={center_lat}, center_long={center_long}")

        if square:
            width = max_long - min_long
            height = max_lat - min_lat
            size = max(width, height)
            log.debug(f"width={width}, height={height}, size={size}")
            min_lat = round(center_lat - size/2, 7)
            max_lat = round(center_lat + size/2, 7)
            min_long = round(center_long - size/2, 7)
            max_long = round(center_long + size/2, 7)
            log.debug2(f"Squared min_lat={min_lat}, min_long={min_long}, max_lat={max_lat}, max_long={max_long}")

        if padding != 0:
            min_lat = round(min_lat - padding / degree_lat_to_miles(center_lat), 7)
            min_long = round(min_long - padding / degree_long_to_miles(center_lat), 7)
            max_lat = round(max_lat + padding / degree_lat_to_miles(center_lat), 7)
            max_long = round(max_long + padding / degree_long_to_miles(center_lat), 7)
            log.debug2(f"Padded min_lat={min_lat}, min_long={min_long}, max_lat={max_lat}, max_long={max_long}")

        return (min_lat, min_long, max_lat, max_long)

    def GetCenter(self):
        """
        Get the coordinates of the center of the tracks in the GPX file.

        Returns:
            (tuple of float) The center coordinates as lat,long.
        """
        min_lat, min_long, max_lat, max_long = self.GetBounds()
        return ( round((max_lat - min_lat)/2, 7), round((max_long - min_long)/2, 7) )

    def GetTrackPoints(self):
        """
        Get all of the points in all of the tracks.

        Returns:
            (list of list of tuple of float) A list of tracks where each track
            is a list of tuples of float as lat,long.
        """
        tracks = []
        root = self._parse()
        for track_node in root.findall("trk", root.nsmap):
            track = []
            for node in track_node.findall("trkseg/trkpt", root.nsmap):
                node_lat = float(node.attrib["lat"])
                node_long = float(node.attrib["lon"])
                track.append((node_lat, node_long))
            tracks.append(track)
        return tracks

    def ToCSV(self, csvfile):
        """
        Convert the GPX tracks to a CSV file.

        Args:
            csvfile:    (string) The file path to save the tracks to.
        """
        root = self._parse()
        with open(csvfile, "w", encoding="utf-8") as outfile:
            outfile.write("LON,LAT\n")
            for node in root.findall("trk/trkseg/trkpt", root.nsmap):
                node_lat = float(node.attrib["lat"])
                node_long = float(node.attrib["lon"])
                outfile.write(f"{node_long},{node_lat}\n")

    def _parse(self):
        if not Path(self.filename).exists():
            raise ApplicationError(f"Could not find file {self.filename}")
        try:
            tree = ET.parse(self.filename)
            return tree.getroot()
        except OSError as ex:
            raise ApplicationError(str(ex)) from ex
        except ET.ParseError as ex:
            raise ApplicationError(f"Error parsing {self.filename}: {ex}") from ex

def degree_long_to_miles(lat):
    """
    Calculate the length of 1 degree of longitude in miles at a given latitude.

    Args:
        lat:    (float) Latitude to measure at, in decimal degrees.

    Returns:
        (float) The length in miles.
    """
    rads = lat * 2 * math.pi / 360
    return (WGS84.p1 * math.cos(rads) + WGS84.p2 * math.cos(3*rads) + WGS84.p3 * math.cos(5*rads)) * 0.000621371

def degree_lat_to_miles(lat):
    """
    Calculate the length of 1 degree of latitude in miles at a given latitude.

    Args:
        lat:    (float) Latitude to measure at, in decimal degrees.

    Returns:
        (float) The length in miles.
    """
    rads = lat * 2 * math.pi / 360
    return (WGS84.m1 + WGS84.m2 * math.cos(2*rads) + WGS84.m3 * math.cos(4*rads) + WGS84.m4 * math.cos(6*rads)) * 0.000621371

def raster_stats(data_source):
    log = GetLogger()

    wgs84 = osr.SpatialReference()
    wgs84.SetFromUserInput('WGS84')
    wgs84.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)

    # Open the image source and get the transforms
    src_sr = osr.SpatialReference()
    src_sr.ImportFromWkt(data_source.GetProjection())
    src_sr.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    gps_to_raster = osr.CoordinateTransformation(wgs84, src_sr)
    raster_to_gps = osr.CoordinateTransformation(src_sr, wgs84)
    gt = data_source.GetGeoTransform()
    fwd_trans = Affine.from_gdal(*gt)
    rev_trans = ~fwd_trans

    # Calculate the size of one pixel in the image source
    i_orig_x, i_orig_y = fwd_trans * (0, 0)
    i_plus1_x, i_plus1_y = fwd_trans * (1, 1)
    plus1_long, plus1_lat, _ = raster_to_gps.TransformPoint(i_plus1_x, i_plus1_y)
    orig_long, orig_lat, _ = raster_to_gps.TransformPoint(i_orig_x, i_orig_y)
    gx_size = plus1_long - orig_long
    gy_size = plus1_lat - orig_lat
    long_feet = degree_long_to_miles(orig_lat) * 5280
    lat_feet = degree_long_to_miles(orig_lat) * 5280
    px_size = long_feet * (plus1_long - orig_long)
    py_size = lat_feet * (plus1_lat - orig_lat)
    log.info(f"  Origin(UL) = {orig_long, orig_lat}")
    log.info(f"  Raster size = {data_source.RasterXSize, data_source.RasterYSize}")
    log.info(f"  One pixel degrees = {gx_size, gy_size}")
    log.info(f"  One pixel feet = {px_size, py_size}")

    i_max_x, i_max_y = fwd_trans * (data_source.RasterXSize, data_source.RasterYSize)
    max_long, min_lat, _ = raster_to_gps.TransformPoint(i_max_x, i_max_y)
    min_long, max_lat, _ = raster_to_gps.TransformPoint(i_orig_x, i_orig_y)
    log.info(f"  alt origin = {min_long, max_lat}")
    log.info(f"  alt max = {max_long, min_lat}")

    # Calculate image/pixel coordinates for the requested crop
    uli_x, uli_y, _ = gps_to_raster.TransformPoint(min_long, max_lat)
    ulp_x, ulp_y = rev_trans * (uli_x, uli_y)

    lri_x, lri_y, _ = gps_to_raster.TransformPoint(max_long, min_lat)
    lrp_x, lrp_y = rev_trans * (lri_x, lri_y)


    gx_size = (max_long - min_long) / float(data_source.RasterXSize)
    gy_size = (max_lat - min_lat) / float(data_source.RasterYSize)
    log.info(f"  alt degree size = {max_long - min_long}")
    log.info(f"  alt one pixel degrees = {gx_size, gy_size}")

    px_per_deg = float(data_source.RasterXSize) / (max_long - min_long)
    log.info(f"  px per degree = {px_per_deg}")
    px_per_deg = float(data_source.RasterYSize) / (max_lat - min_lat)
    log.info(f"  px per degree = {px_per_deg}")

    return {k: v for k,v in locals().items() if not k.startswith("_")}


def align_image_elevation(min_lat, min_long, max_lat, max_long, cache_dir=Path("cache")):
    """
    Compute the nearest GPS coordinates that align to the pixels in the supplied image and elevation rasters.
    This allows the final output model and image to better align
    """
    log = GetLogger()

    # Make a list of image tiles we need to cover this region and download them
    tile_coords = get_image_tile_range(min_lat, min_long, max_lat, max_long)
    image_files = []
    for lat, long in tile_coords:
        try:
            tile_file = download_image_tile(lat, long, cache_dir)
        except (requests.HTTPError, requests.ConnectionError, requests.Timeout) as ex:
            raise ApplicationError(f"Error downloading image: {ex}.\nTry checking your internet connection, or check https://www.sciencebase.gov/catalog/status and https://apps.nationalmap.gov/services-checker/#/uptime") from ex
        image_files.append(tile_file)
    # Create a virtual data source with all of the tiles
    file_list = " ".join(str(f) for f in image_files)
    log.debug(f"Creating virtual data set with tiles [{file_list}]")
    _, image_input_file = tempfile.mkstemp()
    retcode, _, stderr = Shell(f"gdalbuildvrt {image_input_file} {file_list}")
    if retcode != 0 or "ERROR" in stderr:
        raise ApplicationError(f"Could not merge input files: {stderr}")

    # Make a list of elevation tiles we need to cover this region and download them
    tile_coords = get_elevation_tile_range(min_lat, min_long, max_lat, max_long)
    elevation_files = []
    for lat, long in tile_coords:
        try:
            tile_file = download_elevation_tile(lat, long, cache_dir)
        except (requests.HTTPError, requests.ConnectionError, requests.Timeout) as ex:
            raise ApplicationError(f"Error downloading elevation file: {ex}.\nTry checking your internet connection, or check https://www.sciencebase.gov/catalog/status and https://apps.nationalmap.gov/services-checker/#/uptime") from ex
        elevation_files.append(tile_file)
    # Create a virtual data source with all of the tiles
    file_list = " ".join(str(f) for f in elevation_files)
    log.debug(f"Creating virtual data set with tiles [{file_list}]")
    _, elevation_input_file = tempfile.mkstemp()
    retcode, _, stderr = Shell(f"gdalbuildvrt {elevation_input_file} {file_list}")
    if retcode != 0 or "ERROR" in stderr:
        raise ApplicationError(f"Could not merge input files: {stderr}")

    wgs84 = osr.SpatialReference()
    wgs84.SetFromUserInput('WGS84')
    wgs84.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)

    # Open the image source and get the transforms
    img_src_ds = gdal.Open(str(image_input_file))
    GDAL_ERROR.check("Error parsing input file", img_src_ds)
    img_src_sr = osr.SpatialReference()
    img_src_sr.ImportFromWkt(img_src_ds.GetProjection())
    img_src_sr.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    img_gps_to_raster = osr.CoordinateTransformation(wgs84, img_src_sr)
    img_raster_to_gps = osr.CoordinateTransformation(img_src_sr, wgs84)
    img_gt = img_src_ds.GetGeoTransform()
    img_fwd_trans = Affine.from_gdal(*img_gt)
    img_rev_trans = ~img_fwd_trans

    log.info("Source image data")
    img_stats = raster_stats(img_src_ds)

    # Open the elevation source and get the transforms
    dem_src_ds = gdal.Open(str(elevation_input_file))
    GDAL_ERROR.check("Error parsing input file", dem_src_ds)
    dem_src_sr = osr.SpatialReference()
    dem_src_sr.ImportFromWkt(dem_src_ds.GetProjection())
    dem_src_sr.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    dem_gps_to_raster = osr.CoordinateTransformation(wgs84, dem_src_sr)
    dem_raster_to_gps = osr.CoordinateTransformation(dem_src_sr, wgs84)
    dem_gt = dem_src_ds.GetGeoTransform()
    dem_fwd_trans = Affine.from_gdal(*dem_gt)
    dem_rev_trans = ~dem_fwd_trans

    log.info("Source elevation data")
    dem_stats = raster_stats(dem_src_ds)

    img_deg_per_pixel_lat = img_stats["gy_size"]
    img_deg_per_pixel_long = img_stats["gx_size"]

    dem_deg_per_pixel_lat = dem_stats["gy_size"]
    dem_deg_per_pixel_long = dem_stats["gx_size"]

    if img_deg_per_pixel_lat > dem_deg_per_pixel_lat:
        # find the closest pixel in the image, then find corresponding in the dem
        pass
    else:
        # Find the closest pixel in the DEM that completely covers the area N-S
        log.info("Adjusting N-S boundaries")
        log.info(f"  min_long, max_lat = {min_long, max_lat}")
        log.info(f"  min_long, min_lat = {min_long, min_lat}")
        dem_uli_x, dem_uli_y, _ = dem_gps_to_raster.TransformPoint(min_long, max_lat)
        dem_ulp_x, dem_ulp_y = dem_rev_trans * (dem_uli_x, dem_uli_y)
        dem_lli_x, dem_lli_y, _ = dem_gps_to_raster.TransformPoint(min_long, min_lat)
        dem_llp_x, dem_llp_y = dem_rev_trans * (dem_lli_x, dem_lli_y)
        dem_ulp_y = math.floor(dem_ulp_y)
        dem_llp_y = math.ceil(dem_llp_y)
        log.info(f"  dem_ulp_y, dem_llp_y = {dem_ulp_y, dem_llp_y}")
        # Translate back to GPS coords
        adj_dem_lli_x, adj_dem_lli_y = dem_fwd_trans * (dem_llp_x, dem_llp_y)
        _, adj_min_lat, _ = dem_raster_to_gps.TransformPoint(adj_dem_lli_x, adj_dem_lli_y)
        log.info(f"  org_min_lat = {min_lat}")
        log.info(f"  adj_min_lat = {adj_min_lat}")
        adj_dem_uli_x, adj_dem_uli_y = dem_fwd_trans * (dem_ulp_x, dem_ulp_y)
        _, adj_max_lat, _ = dem_raster_to_gps.TransformPoint(adj_dem_uli_x, adj_dem_uli_y)
        log.info(f"  org_max_lat = {max_lat}")
        log.info(f"  adj_max_lat = {adj_max_lat}")

        # Find the closest pixel in the DEM that completely covers the area E-W
        log.info("Adjusting E-W boundaries")
        log.info(f"  max_long, min_lat = {max_long, min_lat}")
        log.info(f"  min_long, min_lat = {max_long, min_lat}")
        dem_lri_x, dem_lri_y, _ = dem_gps_to_raster.TransformPoint(max_long, adj_min_lat)
        dem_lrp_x, dem_lrp_y = dem_rev_trans * (dem_lri_x, dem_lri_y)
        dem_lli_x, dem_lli_y, _ = dem_gps_to_raster.TransformPoint(min_long, adj_min_lat)
        dem_llp_x, dem_llp_y = dem_rev_trans * (dem_lli_x, dem_lli_y)
        log.info(f"  dem_llp_x, dem_lrp_x = {dem_llp_x, dem_lrp_x}")
        dem_llp_x = math.floor(dem_llp_x)
        dem_lrp_x = math.ceil(dem_lrp_x)
        log.info(f"  dem_llp_x, dem_lrp_x = {dem_llp_x, dem_lrp_x}")
        # Translate back to GPS coords
        adj_dem_lli_x, adj_dem_lli_y = dem_fwd_trans * (dem_llp_x, dem_llp_y)
        adj_min_long, _, _ = dem_raster_to_gps.TransformPoint(adj_dem_lli_x, adj_dem_lli_y)
        log.info(f"  org_min_long = {min_long}")
        log.info(f"  adj_min_long = {adj_min_long}")
        adj_dem_lri_x, adj_dem_lri_y = dem_fwd_trans * (dem_lrp_x, dem_lrp_y)
        adj_max_long, _, _ = dem_raster_to_gps.TransformPoint(adj_dem_lri_x, adj_dem_lri_y)
        log.info(f"  org_max_long = {max_long}")
        log.info(f"  adj_max_long = {adj_max_long}")

        adj_max_lat = round(adj_max_lat, 7)
        adj_min_lat = round(adj_min_lat, 7)
        adj_max_long = round(adj_max_long, 7)
        adj_min_long = round(adj_min_long, 7)
        log.info("New coordinates:")
        log.info(f"  -n {adj_max_lat} -w {adj_min_long} -s {adj_min_lat} -e {adj_max_long}")


def get_raster_dimensions(data_source):
    wgs84 = osr.SpatialReference()
    wgs84.SetFromUserInput('WGS84')
    wgs84.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)

    # Open the image source and get the transforms
    src_sr = osr.SpatialReference()
    src_sr.ImportFromWkt(data_source.GetProjection())
    src_sr.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    gps_to_raster = osr.CoordinateTransformation(wgs84, src_sr)
    raster_to_gps = osr.CoordinateTransformation(src_sr, wgs84)
    gt = data_source.GetGeoTransform()
    fwd_trans = Affine.from_gdal(*gt)
    rev_trans = ~fwd_trans

    data = {
        "geo": {},
        "image": {},
        "pixel": {}
    }

    i_orig_x, i_orig_y = fwd_trans * (0, 0)
    i_max_x, i_max_y = fwd_trans * (data_source.RasterXSize, data_source.RasterYSize)
    min_long, max_lat, _ = raster_to_gps.TransformPoint(i_orig_x, i_orig_y)
    max_long, min_lat, _ = raster_to_gps.TransformPoint(i_max_x, i_max_y)

    gx_size = (max_long - min_long) / float(data_source.RasterXSize)
    gy_size = (max_lat - min_lat) / float(data_source.RasterYSize)

    data["geo"]["min_x"] = round(min_long, 7)
    data["geo"]["min_y"] = round(min_lat, 7)
    data["geo"]["max_x"] = round(max_long, 7)
    data["geo"]["max_y"] = round(max_lat, 7)
    data["geo"]["pixel_x"] = gx_size
    data["geo"]["pixel_y"] = gy_size

    data["pixel"]["min_x"] = 0
    data["pixel"]["min_y"] = 0
    data["pixel"]["max_x"] = data_source.RasterXSize
    data["pixel"]["max_y"] = data_source.RasterYSize

    data["image"]["min_x"] = i_orig_x
    data["image"]["min_y"] = i_orig_y
    data["image"]["max_x"] = i_max_x
    data["image"]["max_y"] = i_max_y

    return data

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
    log.debug(f"Cropping to {real_width}mi wide by {real_height}mi high")

    # Transform from GPS coords to pixel
    wgs84 = osr.SpatialReference()
    wgs84.SetFromUserInput('WGS84')
    wgs84.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)

    src_ds = gdal.Open(str(input_filename))
    GDAL_ERROR.check("Error parsing input file", src_ds)
    src_sr = osr.SpatialReference()
    src_sr.ImportFromWkt(src_ds.GetProjection())
    src_sr.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    gps_to_image = osr.CoordinateTransformation(wgs84, src_sr)
    image_to_gps = osr.CoordinateTransformation(src_sr, wgs84)
    gt = src_ds.GetGeoTransform()
    fwd_trans = Affine.from_gdal(*gt)
    rev_trans = ~fwd_trans

    uli_x, uli_y, _ = gps_to_image.TransformPoint(min_long, max_lat)
    ulp_x, ulp_y = rev_trans * (uli_x, uli_y)
    ulp_x, ulp_y = round(ulp_x, 3), round(ulp_y, 3)

    lri_x, lri_y, _ = gps_to_image.TransformPoint(max_long, min_lat)
    lrp_x, lrp_y = rev_trans * (lri_x, lri_y)
    lrp_x, lrp_y = round(lrp_x, 3), round(lrp_y, 3)

    src_data = get_raster_dimensions(src_ds)
    log.debug("Input file")
    log.debug(f"  raster = {src_data['pixel']['max_x'], src_data['pixel']['max_y']}")
    log.debug(f"  ULg = {src_data['geo']['min_x'], src_data['geo']['max_y']}")
    log.debug(f"  ULi = {src_data['image']['min_x'], src_data['image']['max_y']}")
    log.debug(f"  ULp = {src_data['pixel']['min_x'], src_data['pixel']['min_y']}")
    log.debug(f"  LRg = {src_data['geo']['max_x'], src_data['geo']['min_y']}")
    log.debug(f"  LRi = {src_data['image']['max_x'], src_data['image']['min_y']}")
    log.debug(f"  LRp = {src_data['pixel']['max_x'], src_data['pixel']['max_y']}")

    center_lat =  (max_lat + min_lat)/2
    real_width = (max_long - min_long) * degree_long_to_miles(center_lat)
    real_height = (max_lat - min_lat) * degree_lat_to_miles(center_lat)
    log.debug(f"Cropping to {real_width}mi wide by {real_height}mi high")

    # Close the file
    src_ds = None

    # Adjust to the nearest whole pixel so we cleanly crop
    ulp_x = math.floor(ulp_x)
    ulp_y = math.floor(ulp_y)
    lrp_x = math.ceil(lrp_x)
    lrp_y = math.ceil(lrp_y)

    # Crop the input into the requested size
    # Using shell commands for this because the native python interface isn't working after recent GDAL upgrade plus testing agaisnt more types of input files
    # Use shell commands until I have time to debug/fix the native code
    log.info(f"Cropping to boundaries top(max_lat)={max_lat} left(min_long)={min_long} bottom(min_lat)={min_lat} right(max_long)={max_long}")
    log.info(f"Converting to {output_type}")
    log.debug(f"Adjusted to pixel crop boundaries top={ulp_y} left={ulp_x} bottom={lrp_y} right={lrp_x}")
    band_args = "-b 1 -b 2 -b 3" if remove_alpha else ""
    # retcode, _, stderr = Shell(f"gdal_translate -of {output_type} {band_args} -projwin_srs EPSG:4326 -projwin {min_long} {max_lat} {max_long} {min_lat} {input_filename} {output_filename}")
    retcode, _, stderr = Shell(f"gdal_translate -of {output_type} {band_args} -srcwin {ulp_x} {ulp_y} {lrp_x - ulp_x} {lrp_y - ulp_y} {input_filename} {output_filename}")
    if retcode != 0 or "ERROR" in stderr:
        raise ApplicationError(f"Could not crop file: {stderr}")


    # Check the boundaries of the output file we just created
    out_ds = gdal.Open(str(output_filename))
    GDAL_ERROR.check("Error parsing output file", out_ds)

    out_data = get_raster_dimensions(out_ds)
    log.debug("Output file")
    log.debug(f"  raster = {out_data['pixel']['max_x'], out_data['pixel']['max_y']}")
    log.debug(f"  ULg = {out_data['geo']['min_x'], out_data['geo']['max_y']}")
    log.debug(f"  ULi = {out_data['image']['min_x'], out_data['image']['max_y']}")
    log.debug(f"  ULp = {out_data['pixel']['min_x'], out_data['pixel']['min_y']}")
    log.debug(f"  LRg = {out_data['geo']['max_x'], out_data['geo']['min_y']}")
    log.debug(f"  LRi = {out_data['image']['max_x'], out_data['image']['min_y']}")
    log.debug(f"  LRp = {out_data['pixel']['max_x'], out_data['pixel']['max_y']}")


def dem_to_model(dem_filename, model_filename, z_exaggeration=1.0):
    """
    Convert a DEM file to an x3d model file.

    Args:
        dem_filename:       (string) Name of the input DEM data file.
        model_filename:     (string) Name of the output file to create.
        z_exaggeration:     (float)  Multiplier to appy to the Z elevation
                                     values.
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
    Convert a DEM file to an stl model file.

    Args:
        dem_filename:       (string) Name of the input DEM data file.
        model_filename:     (string) Name of the output file to create.
        z_exaggeration:     (float)  Multiplier to appy to the Z elevation
                                     values.
    """
    log = GetLogger()

    if Path(model_filename).suffix != ".stl":
        model_filename += ".stl"

    # Use geotrimesh.generate_terrain to create an scad file from the elevation data
    scad_command_lines = gtm2_generate_terrain(dem_filename, z_scale=z_exaggeration, clippoly_filepath=None)
    scad_command_lines = [str.encode(l) for l in scad_command_lines]

    with tempfile.NamedTemporaryFile(delete=False) as scad_file:
        scad_file.writelines(scad_command_lines)
        scad_file.close()

        # Use openscad to convert the scad file to an STL file
        log.info("  Rendering to STL")
        retcode, _, stderr = Shell(f"openscad -o {model_filename} {scad_file.name}")
        if retcode != 0 or "ERROR" in stderr:
            raise ApplicationError(f"Could not render scad file to stl: {stderr}")

def create_virtual_dataset(file_list):
    """
    Create a virtual dataset using the list of files
    """
    log = GetLogger()
    if list_like(file_list):
        if len(file_list) > 1:
            log.info("Merging input files into virtual data set")
            _, virtual_file = tempfile.mkstemp()
            # The python interface to this is horrifying, so use the command line app
            retcode, _, stderr = Shell("gdalbuildvrt {} {}".format(virtual_file, " ".join(file_list)))
            if retcode != 0 or "ERROR" in stderr:
                raise ApplicationError(f"Could not merge input files: {stderr}")
        else:
            log.debug(f"No virtual dataset to create; directly using {file_list[0]}")
            virtual_file = file_list[0]
    else:
        log.debug(f"No virtual dataset to create; directly using {file_list}")
        virtual_file = file_list

    return virtual_file

def rotate_raster(raster_filename, output_filename, rotation_degrees):
    log = GetLogger()

    src_ds = gdal.Open(str(raster_filename))
    GDAL_ERROR.check("Error parsing input file", src_ds)

    # Make a copy of the original
    _, intermediate_file = tempfile.mkstemp()
    log.debug(f"Using intermediate tempfile {intermediate_file}")
    driver = gdal.GetDriverByName("GTiff")
    dst_ds = driver.CreateCopy(intermediate_file, src_ds, strict=0)

    # Get the center of the raster
    center_x = src_ds.RasterXSize / 2
    center_y = src_ds.RasterYSize / 2

    # Create a rotated geotransform
    gt = src_ds.GetGeoTransform()
    fwd = Affine.from_gdal(*gt)
    rotate = fwd * fwd.rotation(rotation_degrees, (center_x, center_y))

    # Set the transform in the copy
    log.info(f"Rotating raster by {rotation_degrees} degrees")
    dst_ds.SetGeoTransform(rotate.to_gdal())
    # Close the file
    dst_ds = None

    # Run the intermediate file through gdalwarp to make it north-up again
    retcode, _, stderr = Shell(f"gdalwarp -r bilinear -wo \"NUM_THREADS=ALL_CPUS\" {intermediate_file} {output_filename}")
    if retcode != 0 or "ERROR" in stderr:
        raise ApplicationError(f"Failed to process file to north-up: {stderr}")

def get_raster_boundaries_geo(data_source):
    """
    Get the min and max image georeferenced coordinates of the area of the
    image.

    Args:
        data_source:    (osgeo.gdal.Dataset) The GDAL dataset to measure.

    Returns:
        (tuple of float) The bounding box as min_col, min_row, max_col,
        max_row.
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
    Get the min and max image GPS coordinates of the area of the image.

    Args:
        data_source:    (osgeo.gdal.Dataset) The GDAL dataset to measure.

    Returns:
        (tuple of float) The bounding box as as min_lat, min_long, max_lat,
        max_long.
    """
    source_extent = get_raster_boundaries_geo(data_source)
    source_srs = osr.SpatialReference()
    source_srs.ImportFromWkt(data_source.GetProjection())
    source_srs.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    target_srs = osr.SpatialReference()
    target_srs.ImportFromEPSG(4326) # WGS84
    target_srs.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    trans = osr.CoordinateTransformation(source_srs, target_srs)
    min_long, max_lat, _ = trans.TransformPoint(source_extent[0], source_extent[3], 0.0)
    max_long, min_lat, _ = trans.TransformPoint(source_extent[2], source_extent[1], 0.0)
    return (min_lat, min_long, max_lat, max_long)

def get_elevation_tilename(latitude, longitude):
    """Get the filename for a tile covering the given coordinates"""
    upper = int(math.ceil(latitude))
    left = int(math.floor(longitude))
    return f"dem-{get_coords_string(upper, left)}.tif"

def get_image_tile_name(latitude, longitude):
    """Get the filename for an image tile covering the given coordinates"""
    return f"image-{get_coords_string(latitude, longitude)}.jp2"

def get_cropped_elevation_filename(max_lat, min_long, min_lat, max_long):
    """Get the filename for a cropped elevation file for a given area"""
    return f"cropped-dem-{get_coords_string(max_lat, min_long)}_{get_coords_string(min_lat, max_long)}.tif"

def get_cropped_image_filename(max_lat, min_long, min_lat, max_long):
    """Get the filename for a cropped image file for a given area"""
    return f"cropped-image-{get_coords_string(max_lat, min_long)}_{get_coords_string(min_lat, max_long)}.tif"

def get_coords_string(latitude, longitude):
    """Get our standard string representation of lat/long"""
    return "{}{}{}{}".format("n" if latitude > 0 else "s",          #pylint: disable=consider-using-f-string
                                 round(abs(latitude), 7),
                                 "e" if longitude > 0 else "w",
                                 round(abs(longitude), 7))

def download_elevation_tile(latitude, longitude, dest_dir):
    """
    Download elevation data for a given lat/long region. This will download the
    1x1 degree tile covering the requested area, rename it to a standard
    filename, convert it to a GeoTiff, and store it in the destination dir. If
    the file already exists in the destination no action will be taken.

    Args:
        latitude:   (float) Latitude of a point in the region.
        longitude:  (float) Longitude of a point in the region.
        dest_dir:   (Path)  The directory to download the tile to.

    Returns:
        (Path) The full path of the elevation file.
    """
    log = GetLogger()

    upper = int(math.ceil(latitude))
    left = int(math.floor(longitude))

    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)

    # Check the local cache to see if we already have this tile
    output_file = dest_dir / Path(get_elevation_tilename(latitude, longitude))
    log.debug(f"Checking cache for {output_file}")
    if output_file.exists():
        log.info(f"Using cached elevation data for ({upper},{left})")
        return output_file

    # Query the National Map API for elevation products
    log.info(f"Downloading elevation data for ({upper},{left})")
    query_url = "https://tnmaccess.nationalmap.gov/api/v1/products"
    payload = {
        "bbox": f"{left},{upper},{left},{upper}",
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
            log.debug(f"Extracting {local_filename} to {download_dir}")
            with zipfile.ZipFile(local_filename) as archive:
                archive.extractall(path=download_dir)
            # Find the data file in the zip
            coords = get_coords_string(upper, left)
            known_filenames = [f"grd{coords}_13", f"USGS_NED_13_{coords}_IMG.img", f"img{coords}_13.img"]
            for fname in known_filenames:
                if (download_dir / fname).exists():
                    data_filename = download_dir / fname
                    break
        else:
            data_filename = local_filename

        # Convert to GeoTIFF
        retcode, _, stderr = Shell(f"gdal_translate -of GTiff {data_filename} {output_file}")
        if retcode != 0 or "ERROR" in stderr:
            raise ApplicationError(f"Could not convert tile: {stderr}")

        log.info(f"Added elevation data to cache for ({upper},{left})")

        return output_file

def download_image_tile(latitude, longitude, dest_dir):
    """
    Download image file for a given lat/long region. This will download the
    3.75' x 3.75' tile covering the requested area, rename it to a standard
    filename, and store it in the destination dir. If the file already
    exists in the destination no action will be taken.

    Args:
        latitude:   (float) Latitude of a point in the region.
        longitude:  (float) Longitude of a point in the region.
        dest_dir:   (Path)  The directory to download the tile to.

    Returns:
        (Path) The full path of the image file.
    """
    log = GetLogger()

    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)

    # Check the local cache to see if we already have this tile
    output_file = dest_dir / Path(get_image_tile_name(latitude, longitude))
    log.debug(f"Checking cache for {output_file}")
    if output_file.exists():
        log.info(f"Using cached image for ({latitude},{longitude})")
        return output_file

    # Query the National Map API for image products
    log.info(f"Downloading image data for ({latitude},{longitude})")
    query_url = "https://tnmaccess.nationalmap.gov/api/v1/products"
    payload = {
        "bbox": f"{longitude},{latitude},{longitude},{latitude}",
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
        log.info(f"Added image to cache for ({latitude},{longitude})")

        return output_file

def get_image_tile_interval():
    """Get the tile size for image tiles"""
    # USGS distributes elevation data in 3.75' tiles, 3.75' = 0.0625 degree
    return 0.0625

def get_elevation_tile_interval():
    """Get the tile size for elevation tiles"""
    # USGS distributes elevation data in 1 degree tiles
    return 1

def get_image_tile_coords(latitude, longitude):
    """
    Get the coordinates of the upper left corner of the image tile
    containing the point.

    Args:
        latitude:    (float) The latitude of the point.
        longitude:   (float) The longitude of the point.

    Returns:
        (tuple of float) lat,long coordinates of the upper left corner of the
        image tile that covers the point.
    """
    return get_tile_coords_range(latitude, latitude, longitude, longitude, get_image_tile_interval())[0]

def get_image_tile_range(min_lat, min_long, max_lat, max_long):
    """
    Get the coordinates of the upper left corners of the list of image
    tiles containing the region.

    Args:
        min_lat:    (float) The minimum latitude (farthest south).
        min_long:   (float) The minimum longitude (farthest west).
        max_lat:    (float) The maximum latitude (farthest north).
        max_long:   (float) The maximum longitude (farthest east).

    Returns:
        (list of tuple of float) A list of lat,long coordinates of the upper
        left corner of each image tile needed to cover the area.
    """
    return get_tile_coords_range(min_lat, min_long, max_lat, max_long, get_image_tile_interval())

def get_elevation_tile_coords(latitude, longitude):
    """
    Get the coordinates of the upper left corner of the elevation tile
    containing the point.

    Args:
        latitude:    (float) The latitude of the point.
        longitude:   (float) The longitude of the point.

    Returns:
        (tuple of float) lat,long coordinates of the upper left corner of the
        elevation tile that covers the point.
    """
    return get_tile_coords_range(latitude, latitude, longitude, longitude, get_elevation_tile_interval())[0]

def get_elevation_tile_range(min_lat, min_long, max_lat, max_long):
    """
    Get the coordinates of the upper left corners of the list of elevation
    tiles containing the region.

    Args:
        min_lat:    (float) The minimum latitude (farthest south).
        min_long:   (float) The minimum longitude (farthest west).
        max_lat:    (float) The maximum latitude (farthest north).
        max_long:   (float) The maximum longitude (farthest east).

    Returns:
        (list of tuple of float) A list of lat,long coordinates of the upper
        left corner of each elevation tile needed to cover the area.
    """
    return get_tile_coords_range(min_lat, min_long, max_lat, max_long, get_elevation_tile_interval())

def get_tile_coords_range(min_lat, min_long, max_lat, max_long, interval):
    """
    Get the coordinates of the upper left corners of the list tiles containing
    the region.

    Args:
        min_lat:    (float) The minimum latitude (farthest south).
        min_long:   (float) The minimum longitude (farthest west).
        max_lat:    (float) The maximum latitude (farthest north).
        max_long:   (float) The maximum longitude (farthest east).
        interval:   (float) The size of a tile.

    Returns:
        (list of tuple of float) A list of lat,long coordinates of the upper
        left corner of each tile needed to cover the area.
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
    Get the elevation data for the given region. This will download, crop and
    convert the requested elevation data.

    Args:
        dem_filename:   (Path)  File to save the elevation data to.
        min_lat:        (float) South border of the region.
        min_long:       (float) East border of the region.
        max_lat:        (float) North border of the region.
        max_long:       (float) West border of the region.
        cache_dir:      (Path)  The path to save/look for the elevation data.
    """
    log = GetLogger()

    log.debug(f"Checking cache for {cache_dir / dem_filename}")
    if (cache_dir / dem_filename).exists():
        log.info(f"Using cached elevation data file {dem_filename}")
        return

    # Make a list of elevation tiles we need to cover this region and download them
    tile_coords = get_elevation_tile_range(min_lat, min_long, max_lat, max_long)
    elevation_files = []
    for lat, long in tile_coords:
        try:
            tile_file = download_elevation_tile(lat, long, cache_dir)
        except (requests.HTTPError, requests.ConnectionError, requests.Timeout) as ex:
            raise ApplicationError(f"Error downloading image: {ex}.\nTry checking your internet connection, or check https://www.sciencebase.gov/catalog/status and https://apps.nationalmap.gov/services-checker/#/uptime") from ex
        elevation_files.append(tile_file)

    # Create a virtual data source with all of the tiles
    file_list = " ".join(str(f) for f in elevation_files)
    log.debug(f"Creating virtual data set with tiles [{file_list}]")
    _, crop_input_file = tempfile.mkstemp()
    retcode, _, stderr = Shell(f"gdalbuildvrt {crop_input_file} {file_list}")
    if retcode != 0 or "ERROR" in stderr:
        raise ApplicationError(f"Could not merge input files: {stderr}")

    # Crop to the requested region and convert to geotiff
    log.info("Converting and cropping elevation data")
    convert_and_crop_raster(crop_input_file, cache_dir / dem_filename, min_lat, min_long, max_lat, max_long, remove_alpha=False)

def get_image_data(image_filename, min_lat, min_long, max_lat, max_long, cache_dir=Path("cache")):
    """
    Get the imagery data for the given region. This will download, crop and
    convert the requested data.

    Args:
        image_filename: (Path)  File to save the image to.
        min_lat:        (float) South border of the region.
        min_long:       (float) East border of the region.
        max_lat:        (float) North border of the region.
        max_long:       (float) West border of the region.
        cache_dir:      (Path)  The path to save/look for the image data.
    """
    log = GetLogger()

    if (cache_dir / image_filename).exists():
        log.info(f"Using cached image file {image_filename}")
        return

    # Make a list of image tiles we need to cover this region and download them
    tile_coords = get_image_tile_range(min_lat, min_long, max_lat, max_long)
    image_files = []
    for lat, long in tile_coords:
        try:
            tile_file = download_image_tile(lat, long, cache_dir)
        except (requests.HTTPError, requests.ConnectionError, requests.Timeout) as ex:
            raise ApplicationError(f"Error downloading image: {ex}.\nTry checking your internet connection, or check https://www.sciencebase.gov/catalog/status and https://apps.nationalmap.gov/services-checker/#/uptime") from ex
        image_files.append(tile_file)

    # Create a virtual data source with all of the tiles
    file_list = " ".join(str(f) for f in image_files)
    log.debug(f"Creating virtual data set with tiles [{file_list}]")
    _, crop_input_file = tempfile.mkstemp()
    retcode, _, stderr = Shell(f"gdalbuildvrt {crop_input_file} {file_list}")
    if retcode != 0 or "ERROR" in stderr:
        raise ApplicationError(f"Could not merge input files: {stderr}")

    # Crop to the requested region and convert to geotiff
    log.info("Converting and cropping image data")
    convert_and_crop_raster(crop_input_file, cache_dir / image_filename, min_lat, min_long, max_lat, max_long, remove_alpha=True)
