call stack

build_model
dem_to_model
generate_mesh
    ogr_to_elevation_mesh
        parse_polygon
            get_z_coord_of_point
    conv_triangle_shape_to_mesh
        transform_coords
            calculate_ecef_from_lla
        write_x3d

full
-n 38.390 -e -116.0610 -s 38.378 -w -116.0765
upper half
-n 38.390 -e -116.0610 -s 38.384 -w -116.0765
lower right
-n 38.390 -e -116.06875 -s 38.384 -w -116.0765
