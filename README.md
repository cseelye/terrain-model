# 3D Terrain Modelling
This tool will create a 3D model in Blender given a set of GPS coordinates. I built this primarily to take a GPX route from a hike or four wheeling trip and create a full color, printable 3D model.
<p align="center">
<img src="example_blender1.png" alt="blender example 1"/>  <img src="example_blender2.png"  alt="blender example 2"/>
</p>

## Quick Start
These are the typical steps that I follow to create a new model.  All of these commands are run inside the container, with a host directory mounted at "work" to hold the input/output files. *Note this is built to work with The National Map, a US resource, so regions outside the US may not be able to automatically download imagery/elevation data.*

Clone the git repo then launch the container:
```
git clone https://github.com/cseelye/terrain-model.git
cd terrain-model
docker container run --rm -it -v $(pwd):/work -w /work ghcr.io/cseelye/terrain-model
```

1. Download and crop the image(s) and preview what the result looks like. Now is a good time to adjust to exactly the coordinates you want the model to cover using the various options to specify the area. Play around with this until you are happy and get the coordinates exactly right.
```
./prepare_image.py --gpx-file work/hitw.gpx \
                   --padding 0.2 \
                   --track-color red \
                   --track-width 10 \
                   --draw-track \
                   --output-file output/hitw.png
```
<p align="center"><img src="example_image.png" alt="example image"/></p>

2. Download elevation data and create the mesh, using the same coordinates from the previous steps. Try the Z exaggeration if you want to make the features more prominent - sometimes this makes the model more interesting and closer to what it "felt" like in real life in areas without large elevation changes.
```
./build_mesh.py --gpx-file work/hitw.gpx \
                 --padding 0.2 \
                 --z-exaggeration 2 \
                 --mesh-file output/hitw.stl
```
<p align="center"><img src="example_mesh.png" alt="example image"/></p>

3. Convert the mesh to a blender model, size it to something printable, add thickness, square off the bottom, etc.
```
./create_model.py --mesh-file work/hitw.stl \
                  --min-thickness 0.125 \
                  --size 4.5 \
                  --output output/hitw.blend
```

4. The last step isn't polished yet; it hasn't been containerized, so it requires installing Blender 3.1 on your machine, installing the required python modules, running the script directly on your machine (not in the container), and it only runs on macOS. Eventually this step and the previous will be combined into a single script that will run in the container.  
UV map the image onto the model, export and zip the model into a file ready to upload for printing:
```
./finish_model.py --blender-file output/hitw.blend \
                  --map-image output/hitw.png \
                  --background-image output/lightgrey.png
```
Alternately you can manually open the model in blender, UV map the image onto it, and export it as a Collada file. Zip the collada file and image files into a single archive.

<p align="center"><img src="example_blender3.png" alt="example image"/></p>

5. Create a [Shapeways](https://www.shapeways.com) account and upload for printing (when manually uploading, make sure to select "M" for meters as the dimensions when uploading). To use the script for automatic uploading, you will need to register to use the [Shapeways API](https://developers.shapeways.com/manage-apps) and get a client ID and secret.
Upload the archive created from the previous step to Shapeways:
```
./upload_model.py --client-id myClientID \
                  --client-secret myClientSecret \
                  --model-file output/hitw/hitw.zip
```

## Manually Downloading Images/Elevation Data
Download orthoimages from [The National Map](https://apps.nationalmap.gov/downloader)  
1. Select Imagery on the left side
2. Use the map on the right side to search for the area you are trying to model.
3. Click Search Products on the left side.
4. As you hover over the entries on the left, it will highlight the map on the right to show the coverage.
4. Download a many images as you need to cover the area you want to model.
To get elevation data, select "Elevation Products (3DEP)" instead of Imagery.

## Development
See [CONTRIBUTING.md](CONTRIBUTING.md)
