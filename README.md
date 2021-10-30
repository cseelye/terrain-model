# 3D Terrain Modelling
This tool will create a 3D model in Blender given a set of GPS coordinates. This is still a work in progress, so not there are bugs and not everything is automated yet.

I built this primarily to take a GPX route from a hike or four wheeling trip and create a full color, printable 3D model.

## Workflow
This is the typical steps that I follow to create a new model.  All of these commands are run inside the container, with a host directory mounted at "work" to hold the input/output files
1. Download orthoimages from [The National Map](https://apps.nationalmap.gov/downloader)  
    1. Select Imagery on the left side
    2. Use the map on the right side to search for the area you are trying to model.
    3. Click Search Products on the left side.
    4. As you hover over the entries on the left, it will highlight the map on the right to show the coverage.
    4. Download a many images as you need to cover the area you want to model.
2. Crop the image(s) and preview what the result looks like. Now is a good time to adjust to exactly the coordinates you want the model to cover.
```
./crop_geophoto.py --gpx-file work/hitw.gpx \
                   --padding 0.2 \
                   --output-file work/hitw_ortho.tif \
                   --input-file work/m_3911925_se_11_1_20150616_20150923.jp2 \
                   --input-file work/m_3911933_ne_11_1_20150616_20150923.jp2
```
3. Overlay the tracks onto the image and preview it again.
```
./draw_track.py --gpx-file work/hitw.gpx \
                --track-color red \
                --track-width 10 \
                --input-file work/hitw-ortho.tif \
                --output-file work/hitw-track.png
```
4. Create the mesh
```
./build_model.py --gpx-file work/hitw.gpx \
                 --padding 0.2 \
                 --model-file work/hitw.x3d
```
5. Finish the model
```
./refine_model.py --model-file hitw.x3d --output hitw.blend --size 4.75
```
6. Open the model in blender, UV map the image onto it, export it and upload to [shapeways](https://www.shapeways.com) for printing

If you want to really customize the model, stop at step 4, import the x3d into your choice of programs and build it out as you wish.

## About the Container Image
The container image is based on the official GDAL container image release, because I wanted an up-to-date version of GDAL but did not want to spend my time building it myself. At the time of this writing, the official container is based on Ubuntu 20.04 and comes with python 3.8 and the GDAL python libraries installed in that python.

On top of the GDAL container, I build blender from source so that we can get the bpy python library. However, blender is very picky about python compatibility and at the time of this writing I am using blender 2.93 LTS which is using python 3.9.

So in the container there are two python3 versions, each required for a different part of the workflow: python3.8 for the parts that use GDAL and python3.9 for the parts that use blender.

***WARNING:*** Building the container from scratch can take a long time. The blender source repos are very slow to clone for me and can take upwards of 45 min to get all of the code and libraries.
