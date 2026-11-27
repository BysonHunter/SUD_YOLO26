"""Browser artifacts made from the same quantitative results as the desktop UI."""

import json
import time
from pathlib import Path
import numpy as np


def write_json(path, value):
    path = Path(path)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, allow_nan=False), encoding="utf-8"
    )
    # Windows scanners/readers can briefly hold the target without FILE_SHARE_DELETE.
    for attempt in range(10):
        try:
            temporary.replace(path)
            break
        except PermissionError:
            if attempt == 9:
                raise
            time.sleep(0.02 * (attempt + 1))


def scene(extent):
    return dict(
        paper_bgcolor="#ffffff",
        meta=dict(orientation="coronal-z-up-v2"),
        margin=dict(l=0, r=0, b=0, t=35),
        scene=dict(
            aspectmode="data",
            **{
                f"{axis}axis": dict(
                    title=f"{axis.upper()}, мм", range=[0, float(length)]
                )
                for axis, length in zip("xyz", extent)
            },
            camera=dict(
                eye=dict(x=0, y=-2, z=0),
                up=dict(x=0, y=0, z=1),
                projection=dict(type="orthographic"),
            ),
        ),
    )


def mesh_trace(points, triangles, **options):
    # Plain JSON arrays avoid Plotly 6 binary-array encoding in independently served artifacts.
    return dict(
        type="mesh3d",
        **{a: points[:, i].round(4).tolist() for i, a in enumerate("xyz")},
        **{a: triangles[:, i].tolist() for i, a in enumerate("ijk")},
        **options,
    )


def stone_model(volume, spacing, cut=False):
    from sud_yolo26.visualization.stone_geometry import (
        cube_xyz,
        cutaway,
        density_layers,
        surface_mesh,
    )
    from matplotlib import colormaps

    full = cube_xyz(volume)
    cube = cutaway(full) if cut else full
    traces = []
    cmap = colormaps["afmhot"]
    colors = [
        [i / 16, "rgb(%d,%d,%d)" % tuple(int(v * 255) for v in cmap(i / 16)[:3])]
        for i in range(17)
    ]
    for band, opacity in density_layers(cube, reference=full):
        points, triangles, intensity = surface_mesh(
            band, spacing, occluded=cube > band.max()
        )
        # Display-only simplification; quantities and HU masks are never resampled.
        if len(triangles) > 40000:
            import pyvista as pv

            mesh = pv.PolyData(points, np.c_[np.full(len(triangles), 3), triangles])
            mesh.point_data["HU"] = intensity
            mesh = mesh.clean().decimate_pro(
                1 - 40000 / len(triangles),
                preserve_topology=True,
                splitting=False,
                boundary_vertex_deletion=False,
            )
            points, triangles, intensity = (
                mesh.points,
                mesh.faces.reshape(-1, 4)[:, 1:],
                mesh["HU"],
            )
        traces.append(
            mesh_trace(
                points,
                triangles,
                intensity=np.asarray(intensity).tolist(),
                intensitymode="vertex",
                colorscale=colors,
                cmin=160,
                cmax=1350,
                opacity=opacity,
                flatshading=True,
                showscale=not traces,
                name=f"{band[band>0].min():.0f}–{band.max():.0f} HU",
                showlegend=True,
                colorbar=dict(title="HU"),
                lighting=dict(ambient=0.8, diffuse=0.2, specular=0),
            )
        )
    return dict(data=traces, layout=scene(np.array(full.shape) * spacing))


def kidney_model(volume, spacing):
    import pyvista as pv
    from sud_yolo26.visualization.stone_geometry import cube_xyz
    from sud_yolo26.visualization.desktop import (
        kidney_context_surface,
        compact_kidney_surface,
    )

    cube = cube_xyz(volume)
    traces = []
    for factory, color, opacity, title in [
        (kidney_context_surface, "#e6a0b7", 0.38, "Оболочка ROI почки"),
        (compact_kidney_surface, "#e78932", 1.0, "Участки HU >160"),
    ]:
        try:
            mesh = factory(pv, cube, spacing).triangulate()
        except ValueError:
            continue
        if mesh.n_cells:
            traces.append(
                mesh_trace(
                    mesh.points,
                    mesh.faces.reshape(-1, 4)[:, 1:],
                    color=color,
                    opacity=opacity,
                    name=title,
                    showlegend=True,
                    lighting=dict(ambient=0.6, diffuse=0.7, specular=0.2),
                )
            )
    return dict(data=traces, layout=scene(np.array(cube.shape) * spacing))


def laser_table(mass):
    from sud_yolo26.reporting.pdf_report import _calc_laser_table
    from sud_yolo26.ui_workspace.laser_legend import (
        pdf_cell_colour,
        colour_mode,
        MODE_LEGEND,
    )

    frequencies, energies, values, bands = _calc_laser_table(mass)
    cells = []
    for row in values:
        cells.append([])
        for v in row:
            band = pdf_cell_colour(v, bands)
            cells[-1].append(
                dict(
                    value=float(v),
                    background=list(band["background"]),
                    color=list(band["text"]),
                    mode=colour_mode(band["background"]),
                )
            )
    return dict(
        frequencies=frequencies,
        energies=energies,
        cells=cells,
        legend=[dict(color=list(color), mode=mode) for color, mode in MODE_LEGEND],
    )


def series_artifacts(root, export, result, warnings=None):
    root, export = Path(root).resolve(), Path(export).resolve()
    relative = lambda path: Path(path).resolve().relative_to(root).as_posix()
    from sud_yolo26.visualization.stone_geometry import legacy_spacing_xyz

    selection = json.loads((export / "selection.json").read_text(encoding="utf-8"))
    data = dict(
        identity={key: str(value)[:256] for key, value in selection["identity"].items()},
        images={},
        stones=dict(right=[], left=[]),
        warnings=warnings or [],
        _kidney_models={},
    )
    for projection in ("axial", "coronal"):
        images = sorted(
            (export / "images" / projection).glob("*.png"),
            key=lambda p: int(p.stem.rsplit("_", 1)[1]),
        )
        records = []
        for image in images:
            plane = json.loads(
                (export / "geometry/planes" / f"{image.stem}.json").read_text(
                    encoding="utf-8"
                )
            )
            record = dict(
                layer=plane["slice_index"],
                width=plane["width"],
                height=plane["height"],
                sx_mm=float(np.linalg.norm(plane["column_step_lps_mm"])),
                sy_mm=float(np.linalg.norm(plane["row_step_lps_mm"])),
                _file=relative(image),
                detected=(image.parent / "detect" / image.name).is_file(),
            )
            if record["detected"]:
                record["_annotated"] = relative(image.parent / "detect" / image.name)
            records.append(record)
        data["images"][projection] = records
    if result is None:
        data["analysis_available"] = False
        return data
    data["analysis_available"] = True
    data["assembly_version"] = result.get("assembly_version")
    output = Path(result["stones_dir_path"])
    spacing = legacy_spacing_xyz(result["param_numpy"])
    for side, prefix, key in [
        ("right", "rk", "RS_params"),
        ("left", "lk", "LS_params"),
    ]:
        kidney = output / ("RK.npy" if side == "right" else "LK.npy")
        if kidney.is_file():
            volume = np.load(kidney, allow_pickle=False)
            if volume.size > 1:
                model = kidney_model(volume, spacing)
                if model["data"]:
                    target = output / f"web-kidney-{side}.json"
                    write_json(target, model)
                    data["_kidney_models"][side] = relative(target)
        data.setdefault("kidney_available", {})[side] = side in data["_kidney_models"]
        for i, p in enumerate(result[key]):
            volume = np.load(output / f"st_{prefix}_{i}.npy", allow_pickle=False)
            stone = dict(
                index=i,
                layer=int(p[1]),
                dimensions_mm=[float(p[2]) * 10, float(p[3]) * 10, float(p[4]) * 10],
                volume_cm3=float(p[8]),
                voxel_count=int(p[9]),
                mass_g=float(p[10]),
                density_g_cm3=float(p[11]),
                hu_max=float(p[13]),
                hu_min=float(p[14]),
                hu_mean=float(p[15]),
                layers=[int(p[20]), int(p[21]) - 1],
                laser=laser_table(float(p[10])),
                _previews={},
            )
            for kind, name in [
                ("slice", f"stone_{prefix}_{i}.png"),
                ("hu", f"stone{prefix}_{i}_1.png"),
                ("density", f"stone{prefix}_{i}_2.png"),
            ]:
                path = output / name
                if path.is_file():
                    stone["_previews"][kind] = relative(path)
            for cut in (False, True):
                path = output / f'web-stone-{prefix}-{i}{"-cut" if cut else ""}.json'
                write_json(path, stone_model(volume, spacing, cut))
                stone["_cut_model" if cut else "_model"] = relative(path)
            data["stones"][side].append(stone)
    data["_pdf"] = relative(result["pdfFileName"])
    return data
