# 3D Terrain Modelling
This tool will create a 3D model in Blender given a set of GPS coordinates. This is still a work in progress, so it should work pretty well but there are occasional bugs and not everything is automated yet.

I built this primarily to take a GPX route from a hike or four wheeling trip and create a full color, printable 3D model.

![model preview](blender_preview.png)

## Workflow
This is the typical steps that I follow to create a new model.  All of these commands are run inside the container, with a host directory mounted at "work" to hold the input/output files.

From the repo directory, prepare and enter the container:
```
docker image build --target prod -t terrain-model .
docker container run --rm -it -v $(pwd):/work -w /work terrain-model
```

1. Download the images to cover the area. This will download all of the necessary image tiles from USGS [National Map](https://apps.nationalmap.gov/downloader) into a local cache.
```
./download_image_data.py --gpx-file work/hitw.gpx \
                         --padding 0.2
```
2. Crop the image(s) and preview what the result looks like. Now is a good time to adjust to exactly the coordinates you want the model to cover using the various options to specify the area. Play around with this until you are happy and get the coordinates exactly right.
```
./prepare_image.py --gpx-file work/hitw.gpx \
                   --padding 0.2 \
                   --color red \
                   --track-width 10 \
                   --draw-track \
                   --max-height 2048 \
                   --max-width 2048 \
                   --output-file output/hitw.png
```
3. Download the elevation data, using the same coordinates from the previous step.
```
./download_elevation_data.py --gpx-file work/hitw.gpx \
                             --padding 0.2
```
4. Create the mesh, using the same coordinates from the previous steps. Try the Z exaggeration if you want to make the mountains more prominent. I find this makes the model more interesting and closer to what it "felt" like in real life in areas without large elevation changes.
```
./build_model.py --gpx-file work/hitw.gpx \
                 --padding 0.2 \
                 --z-exaggeration 2 \
                 --model-file work/hitw.stl
```
5. Convert the mesh to a blender model, size it to something printable, add thickness, square off the bottom, etc.
```
./refine_model.py --model-file hitw.stl \
                  --min-thickness 0.125 \
                  --size 4.5 \
                  --output hitw.blend
```
6. Open the model in blender, UV map the image onto it, export it and upload to [shapeways](https://www.shapeways.com) for printing.

If you want to really customize the model, stop at step 4, import the stl into your choice of programs and build it out as you wish.

## Manually Downloading Images/Elevation Data
Download orthoimages from [The National Map](https://apps.nationalmap.gov/downloader)  
1. Select Imagery on the left side
2. Use the map on the right side to search for the area you are trying to model.
3. Click Search Products on the left side.
4. As you hover over the entries on the left, it will highlight the map on the right to show the coverage.
4. Download a many images as you need to cover the area you want to model.
To get elevation data, select "Elevation Products (3DEP)" instead of Imagery.

## About the Container Image
The container image is based on the official GDAL container image release, because I wanted an up-to-date version of GDAL but did not want to spend my time building it myself. At the time of this writing, the official container is based on Ubuntu 20.04 and comes with python 3.8 and the GDAL python libraries installed in that python.

On top of the GDAL container, I build blender from source so that we can get the bpy python library. However, blender is very picky about python compatibility and at the time of this writing I am using blender 2.93 LTS which is using python 3.9.

So in the container there are two python3 versions, each required for a different part of the workflow: python3.8 for the parts that use GDAL and python3.9 for the parts that use blender.

***WARNING:*** Building the container from scratch can take a long time. The blender source repos are sometimes very slow to clone for me and can take upwards of 45 min to get all of the code and libraries.

## Development
If you are hacking on the scripts, you probably want to use the dev target in the Dockerfile:
```
docker image build --target dev -t terrain-model-dev .
docker container run --rm -it -v $(pwd):/work -w /work terrain-model-dev
```
