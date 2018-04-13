#!/usr/bin/env python2.7
"""Draw GPX tracks on top of a geospatial image"""

from pyapputil.appframework import PythonApp
from pyapputil.argutil import ArgumentParser
from pyapputil.typeutil import ValidateAndDefault, StrType, PositiveNonZeroIntegerType
from pyapputil.logutil import GetLogger, logargs
import cv2
from osgeo import gdal, osr
import numpy as np
import webcolors

from geo import GPXFile

@logargs
@ValidateAndDefault({
    # "arg_name" : (arg_type, arg_default)
    "gpx_file" : (StrType, None),
    "track_color" : (StrType, None),
    "track_width" : (PositiveNonZeroIntegerType, 10),
    "input_file" : (StrType, None),
    "output_file" : (StrType, None),
})
def main(gpx_file,
         track_color,
         track_width,
         input_file,
         output_file):
    """
    Combine an orthoimage and a GPX file into a PNG with the image as the background
    and the tracks from the GPX file drawn over it.

    Args:
        gpx_file:       (str) GPX format file containing one or more tracks to draw
        track_color:    (str) The color to draw the tracks in, either by name or as RGB tuple
        track_width:    (int) Width of the track, in pixels
        input_file:     (str) Geospatial image to draw the tracks on
        output_file:    (str) Output file to create
    """

    log = GetLogger()

    if "," in track_color:
        # Try to parse as an RBG tuple
        try:
            color_bgr = [int(p.strip()) for p in track_color.split(',')]
            color_bgr.reverse()
        except ValueError:
            log.error("{} is not a valid RBG tuple".format(track_color))
    else:
        # Try to parse as a color name
        try:
            color = webcolors.name_to_rgb(track_color)
            color_bgr = (color.blue, color.green, color.red)
        except ValueError:
            log.error("{} is not a recognizable color name".format(track_color))

    if not output_file.endswith("png"):
        output_file += ".png"

    # Reproject our GPS coordinates into the spatial system of our source raster
    log.info("Reprojecting coordinates")
    src_ds = gdal.Open(input_file)
    wgs84 = osr.SpatialReference()
    wgs84.SetFromUserInput('WGS84')
    src_sr = osr.SpatialReference()
    src_sr.ImportFromWkt(src_ds.GetProjection())
    trans = osr.CoordinateTransformation(wgs84, src_sr)
    gt = src_ds.GetGeoTransform()

    # Draw the track
    log.info("Drawing track on image")
    img = cv2.imread(input_file)
    gpx = GPXFile(gpx_file)
    for track in gpx.GetTrackPoints():
        geo_points = [(x, y) for x, y, _ in trans.TransformPoints([(y, x) for x, y in track])]
        pixel_points = np.array([((x - gt[0]) / gt[1], (y - gt[3]) / gt[5]) for x, y in geo_points], np.int32)
        pixel_points = pixel_points.reshape((-1, 1, 2))
        cv2.polylines(img, [pixel_points], False, color_bgr, track_width, cv2.CV_AA)

    cv2.imwrite(output_file, img)
    log.passed("Successfully wrote {}".format(output_file))


if __name__ == '__main__':
    parser = ArgumentParser(description="Combine an orthoimage and GPX file into a PNG with the image as background and the tracks drawn over it")
    parser.add_argument("-g", "--gpx-file", type=StrType, metavar="FILENAME", help="GPX file to use")
    parser.add_argument("-c", "--track-color", type=StrType, metavar="COLOR", help="The color to draw the track in, either a name or RGB tuple")
    parser.add_argument("-r", "--track-width", type=PositiveNonZeroIntegerType, default=10, metavar="PIXELS", help="The width of the track to draw, in pixels")
    parser.add_argument("-i", "--input-file", type=StrType, metavar="FILENAME", help="Input file, in a raster format that GDAL can read")
    parser.add_argument("-o", "--output-file", type=StrType, metavar="FILENAME", help="Output file (PNG format)")
    args = parser.parse_args_to_dict()

    app = PythonApp(main, args)
    app.Run(**args)
