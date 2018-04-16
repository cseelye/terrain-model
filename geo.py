"""Helpers for processing GIS data into 3D models"""

from gtm.geotrimesh import mesh
from pyapputil.logutil import GetLogger
from affine import Affine
from lxml import etree as ET
import math
import os
from osgeo import gdal, ogr, osr
import tempfile

gdal.UseExceptions()

# Latitude:   -90 to 90 degrees, north positive, south negative
# Longtitude: -180 to 180 degrees, east positive, west negative

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
        input_filename:     the name of the file to convert (string)
        output_filename:    the name of the new file to create (string)
        min_lat:            the south edge of the new file, in decimal degrees (float)
        min_long:           the west edge of the new file, in decimal degrees (float)
        max_lat:            the north edge of the new file, in decimal degrees (float)
        max_long:           the east edge of the new file, in decimal degrees (float)
        output_type:        the data format of the new file (string)
        remove_alpha:       remove the transparency from the new file, only for images (bool)
    """

    log = GetLogger()

    ds = gdal.Open(input_filename)
    with tempfile.NamedTemporaryFile() as tf:
        log.info("Cropping to boundaries top={} left={} bottom={} right={}".format(max_lat, min_long, min_lat, max_long))
        input_srs = osr.SpatialReference()
        input_srs.ImportFromWkt(ds.GetProjection())
        output_srs = input_srs.CloneGeogCS()
        gdal.Warp(tf.name, ds, outputBounds=(min_long, min_lat, max_long, max_lat), outputBoundsSRS=output_srs, multithread=True, srcAlpha=False, dstAlpha=False)

        log.info("Converting to {}".format(output_type))
        temp_ds = gdal.Open(tf.name)
        band_list = None
        if remove_alpha:
            band_list = range(1, temp_ds.RasterCount)
        gdal.Translate(output_filename, temp_ds, format=output_type, bandList=band_list)


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
    elevation.generate_mesh(dem=dem_filename,
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
    target_srs = source_srs.CloneGeogCS()
    trans = osr.CoordinateTransformation(source_srs, target_srs)
    min_long, max_lat, zval = trans.TransformPoint(source_extent[0], source_extent[3], 0.0)
    max_long, min_lat, zval = trans.TransformPoint(source_extent[2], source_extent[1], 0.0)
    return (min_lat, min_long, max_lat, max_long)
