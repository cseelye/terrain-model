# 3D Terrain Modelling
This tool will create a 3D model in Blender given a set of GPS coordinates. This is still a work in progress, so not
all of the steps are automated yet.

I built this primarily to take a GPX route from a hike or four wheeling trip and create a full color, printable 3D model.

## Workflow
This is the typical steps that I follow to create a new model.  All of these commands are run inside the container, with
a host directory mounted at "work" to hold the input/output files
1. Create the X3D model
```
./build_model.py --gpx-file work/hitw.gpx \
                 --padding 0.2 \
                 --model-file work/hitw.x3d
```
2. Download orthoimages from [The National Map](https://viewer.nationalmap.gov/basic/)
3. Merge images if necessary to cover the entire area being modelled
```
gdal_merge.py -of GTiff \
              -o work/merged.tif \
              work/m_3911925_se_11_1_20150616_20150923.jp2 \
              work/m_3911933_ne_11_1_20150616_20150923.jp2
```
4. Crop the image
```
./crop_geophoto.py --gpx-file work/hitw.gpx \
                   --padding 0.2 \
                   --input-file work/merged.tif \
                   --output-file work/cropped.tif
```
5. Overlay the tracks onto the image
```
./draw_track.py --gpx-file work/hitw.gpx \
                --track-color red \
                --track-width 10 \
                --input-file work/cropped.tif \
                --output-file work/hitw.png
```
6. Import the X3D model into blender, add some thickness to it, UV map the image onto it
