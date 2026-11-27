import csv
import os
import pickle
import json
from os import listdir
from pathlib import Path
from sys import maxsize as max_int_size

# import PySimpleGUI as sg
import cv2
import matplotlib.pyplot as plt
import numpy as np
from matplotlib import cm
# Quality placeholders are deliberately not random.

from sud_yolo26.application import state as kgi
from sud_yolo26.domain import constants as Constants
from sud_yolo26.domain import geometry as Utils
from sud_yolo26.settings import service as cf
from sud_yolo26.reporting import pdf_report as pw
from sud_yolo26.visualization import rendering as v3d
from sud_yolo26.visualization.stone_geometry import legacy_spacing_xyz, cube_xyz
from sud_yolo26.domain.dto import *
from .assembly import ASSEMBLY_VERSION, assemble_stone, assembly_conflicts, new_result_directory, consolidate_clusters
from .input_data import load_geometry_input, signature_files
# matplotlib.use('TkAgg')

min_int_size = -max_int_size - 1


def stone_layer_range(first_index, last_index, depth):
    """Detection cluster endpoints are inclusive; numpy slice stops are exclusive."""
    start = max(0, int(first_index))
    stop = min(int(depth), int(last_index)+1)
    if start >= stop:
        raise ValueError('Диапазон слоёв камня не пересекается с исходным объёмом')
    return start, stop


def dens_stone_volume_from_hu(only_stone):
    """Плотность по вокселям камня (как в calc_stone_parameters)."""
    dens_stone = []
    dens_stone_sum = 0
    for z in range(only_stone.shape[0]):
        for y in range(only_stone.shape[1]):
            for x in range(only_stone.shape[2]):
                if only_stone[z, y, x] != 0:
                    d = (only_stone[z, y, x] * 0.000485 + 1.539)
                    dens_stone.append(d)
                    dens_stone_sum += d
                else:
                    dens_stone.append(0)
    dens_stone = np.array(dens_stone).reshape(only_stone.shape)
    return dens_stone, dens_stone_sum


def new_dens_stone_from_density(dens_stone):
    """Дискретизация плотности для второй 3D-визуализации (как в calc_stone_parameters)."""
    new_dens_stone = []
    for z in range(dens_stone.shape[0]):
        for y in range(dens_stone.shape[1]):
            for x in range(dens_stone.shape[2]):
                if dens_stone[z, y, x] == 0:
                    new_dens_stone.append(0)
                elif (dens_stone[z, y, x] < 1.75) and (dens_stone[z, y, x] > 0):
                    new_dens_stone.append(1.7)
                elif (dens_stone[z, y, x] >= 1.75) and (dens_stone[z, y, x] < 1.85):
                    new_dens_stone.append(1.8)
                elif (dens_stone[z, y, x] >= 1.85) and (dens_stone[z, y, x] < 1.95):
                    new_dens_stone.append(1.9)
                elif (dens_stone[z, y, x] >= 1.95) and (dens_stone[z, y, x] < 2.05):
                    new_dens_stone.append(2.0)
                elif (dens_stone[z, y, x] >= 2.05) and (dens_stone[z, y, x] < 2.15):
                    new_dens_stone.append(2.1)
                elif (dens_stone[z, y, x] >= 2.15) and (dens_stone[z, y, x] < 2.25):
                    new_dens_stone.append(2.2)
                elif (dens_stone[z, y, x] >= 2.25) and (dens_stone[z, y, x] < 2.35):
                    new_dens_stone.append(2.3)
                elif dens_stone[z, y, x] >= 2.35:
                    new_dens_stone.append(2.4)
    return np.array(new_dens_stone).reshape(dens_stone.shape)


def _cache_file_path(input_path) -> str:
    base = Path(str(input_path))
    return str(base / "stones" / "stones_result_cache_assembly_v2.pkl")


def assembly_options():
    import yaml
    from sud_yolo26.application.paths import CONFIG_DIR
    path = CONFIG_DIR/'analysis.yaml'
    settings = yaml.safe_load(path.read_text(encoding='utf-8')) if path.is_file() else {}
    padding = float((settings or {}).get('stone_roi_padding_mm',2.))
    if not np.isfinite(padding) or padding<0:
        raise ValueError('stone_roi_padding_mm должен быть неотрицательным числом')
    return dict(padding_mm=padding)


def _detection_signature(input_path):
    import hashlib
    base = Path(input_path)
    manifest = base / "detect" / "detection_manifest.json"
    labels = sorted((base / 'detect' / 'labels').glob('*.txt'))
    data_files=signature_files(base)
    if not manifest.is_file() and (not labels or not data_files):
        return None
    digest = hashlib.sha256()
    digest.update(ASSEMBLY_VERSION.encode('ascii'))
    digest.update(json.dumps(assembly_options(),sort_keys=True).encode('utf-8'))
    digest.update(b'geometry-backed-input-v1')
    sources = ([manifest] if manifest.is_file() else []) + labels + data_files
    for path in sources:
        digest.update(os.path.relpath(path,base).replace('\\','/').encode("utf-8"))
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
    return digest.hexdigest()


def load_cached_result(input_path):
    cache_file = _cache_file_path(input_path)
    if not Path(cache_file).is_file():
        return None
    try:
        with open(cache_file, "rb") as f:
            value = pickle.load(f)
            signature = _detection_signature(input_path)
            if (not signature or value.get("detection_signature") != signature
                    or value.get('assembly_version') != ASSEMBLY_VERSION
                    or not Path(value.get('stones_dir_path','')).is_dir()):
                return None
            return value
    except Exception:
        return None


def main(input_path, show_ui=True, output_dir=None):
    lang_of_interface, path_to_yolo_weights, default_input_dicom_folder, default_output_folder, current_color_theme, \
        img_count, img_format = cf.read_settings()
    tooltips, buttons, list_text, checkbox, main_menu, window_heads, titles = cf.set_language(lang_of_interface)

    # noinspection PyMethodMayBeStatic
    class Parser:
        """ нужно ли парсить строку файла """
        def parse_condition(self, line):
            first_token = line.split(' ')[0]
            return first_token.isdigit()

        ''' преобразование строки в объект '''
        def line_transform(self, line):
            tokens = list(map(lambda x: float(x), line.split(' ')))
            return SliceDTO(
                label=tokens[0],
                x=tokens[1],
                z=tokens[2],
                w=tokens[3],
                h=tokens[4],
                conf=tokens[5]
            )

        ''' парсинг имени файла '''
        def filename_parser(self, filename):
            # return int(filename[5:8]) # для старой разметки имен файлов
            return int(Path(filename).stem.rsplit("_", 1)[-1])

        ''' парсинг файла '''
        def parse(self, path, filename):
            with open(path + filename, 'r', encoding="ISO-8859-1") as file:
                lines = [line for line in file.readlines() if self.parse_condition(line)]
                y = self.filename_parser(filename)
                layer = LayerDTO(y, list(map(self.line_transform, lines)))
                return layer

    class kidney_type:
        normal = 0
        pieloectasy = 1

    def get_kidney_info(kidney):
        first = max_int_size
        last = min_int_size
        max_square = min_int_size
        max_square_index = 0
        max_square_center = (0, 0)
        max_w = min_int_size
        max_h = min_int_size
        size = 0
        cur_kidney_type = kidney_type.normal
        for (key, value) in filter(lambda x: x[1], kidney.items()):
            size += 1
            first = min(key, first)
            last = max(key, last)
            cur_square = value.w * value.h
            if cur_square > max_square:
                max_square = cur_square
                max_square_index = key
                max_square_center = (value.x, value.z)
                max_w = value.w
                max_h = value.h

            if value.label == Constants.left_kidney_pieloectasy or value.label == Constants.right_kidney_pieloectasy:
                cur_kidney_type = kidney_type.pieloectasy

        return ObjectParamsDto(
            x_center=max_square_center[0],
            z_center=max_square_center[1],
            w=max_w,
            h=max_h,
            number=max_square_index,
            first_index=first,
            last_index=last
        )

    ''' находится ли объект в ограничениях для правой почки '''
    def right_kidney_condition(slice):
        return Utils.is_right_kidney(slice) and Utils.is_in_right_kidney_constraints(slice)

    ''' находится ли объект в ограничениях для левой почки '''
    def left_kidney_condition(slice):
        return Utils.is_left_kidney(slice) and Utils.is_in_left_kidney_constraints(slice)

    ''' выбрать почку с максимальным правдоподобием '''
    def kidney_with_max_conf(lst):
        if not len(lst):
            return None
        return max(lst, key=lambda x: x.conf)

    ''' выбрать правую почку с максимальным правдоподобием '''
    def right_kidney_with_max_conf(lst):
        return kidney_with_max_conf(list(filter(right_kidney_condition, lst)))

    ''' выбрать левую почку с максимальным правдоподобием'''
    def left_kidney_with_max_conf(lst):
        return kidney_with_max_conf(list(filter(left_kidney_condition, lst)))

    def get_kidney_array(kidney_list, light_array):
        kidney_info = get_kidney_info(kidney_list)
        x_begin_scaled, x_end_scaled, z_begin_scaled, z_end_scaled = Utils.get_indexes_from_object(
            kidney_list[kidney_info.number], light_array)
        return light_array[z_begin_scaled:z_end_scaled, kidney_info.first_index:kidney_info.last_index,
               x_begin_scaled:x_end_scaled]

    def stone_clusterize(stone_dict):
        # Пересобираем камни как 3D-компоненты связности.
        # Это позволяет объединять камни сложной формы в единый объект,
        # даже если контур на соседних срезах немного "гуляет".
        nodes = []  # [(slice_idx, slice_obj), ...]
        for slice_idx, layer in stone_dict.items():
            for s in layer:
                nodes.append((slice_idx, s))

        if not nodes:
            return []

        parent = list(range(len(nodes)))
        rank = [0] * len(nodes)

        def find(x):
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def union(a, b):
            ra, rb = find(a), find(b)
            if ra == rb:
                return
            if rank[ra] < rank[rb]:
                parent[ra] = rb
            elif rank[ra] > rank[rb]:
                parent[rb] = ra
            else:
                parent[rb] = ra
                rank[ra] += 1

        def expanded_overlap(s1, s2, margin=0.12):
            # bbox в нормализованных координатах [0..1]
            x1_min = s1.x - s1.w / 2
            x1_max = s1.x + s1.w / 2
            z1_min = s1.z - s1.h / 2
            z1_max = s1.z + s1.h / 2

            x2_min = s2.x - s2.w / 2
            x2_max = s2.x + s2.w / 2
            z2_min = s2.z - s2.h / 2
            z2_max = s2.z + s2.h / 2

            # Расширяем рамки, чтобы не рвать сложные формы на куски
            # при небольшом смещении между соседними срезами.
            mx = margin * max(s1.w, s2.w)
            mz = margin * max(s1.h, s2.h)
            x1_min, x1_max = x1_min - mx, x1_max + mx
            z1_min, z1_max = z1_min - mz, z1_max + mz
            x2_min, x2_max = x2_min - mx, x2_max + mx
            z2_min, z2_max = z2_min - mz, z2_max + mz

            inter_x = min(x1_max, x2_max) - max(x1_min, x2_min)
            inter_z = min(z1_max, z2_max) - max(z1_min, z2_min)
            return inter_x > 0 and inter_z > 0

        # Соединяем детекции только в пределах близких срезов:
        # - тот же срез (иногда модель дублирует один камень);
        # - соседние срезы;
        # - через один (чтобы переживать "дырки" в детекции).
        for i in range(len(nodes)):
            y_i, s_i = nodes[i]
            for j in range(i + 1, len(nodes)):
                y_j, s_j = nodes[j]
                if abs(y_i - y_j) > 2:
                    continue
                if (
                    Utils.is_in_other_slice(s_i, s_j)
                    or Utils.is_in_other_slice(s_j, s_i)
                    or expanded_overlap(s_i, s_j)
                ):
                    union(i, j)

        groups = {}
        for idx, (slice_idx, s) in enumerate(nodes):
            root = find(idx)
            if root not in groups:
                groups[root] = {}
            groups[root].setdefault(slice_idx, []).append(s)

        return list(groups.values())

    def stone_info(stone_list):
        array = load_numpy_array()
        res = []
        for (i, stone) in enumerate(stone_list):
            first = max_int_size
            last = min_int_size
            max_light = min_int_size
            max_light_index = 0
            max_light_center = (0, 0)
            max_light_params = (0, 0)
            for (key, layer) in stone.items():
                first = min(key, first)
                last = max(key, last)
                for cur_slice in layer:
                    x_begin_scaled, x_end_scaled, z_begin_scaled, z_end_scaled = Utils.get_indexes_from_object(
                        cur_slice,
                        array)
                    cur_light = array[z_begin_scaled:z_end_scaled, key, x_begin_scaled:x_end_scaled].sum()
                    if cur_light > max_light:
                        max_light = cur_light
                        max_light_index = key
                        max_light_center = (cur_slice.x, cur_slice.z)
                        max_light_params = (cur_slice.w, cur_slice.h)
            res.append(ObjectParamsDto(
                x_center=max_light_center[0],
                z_center=max_light_center[1],
                w=max_light_params[0],
                h=max_light_params[1],
                number=max_light_index,
                first_index=first,
                last_index=last
            ))
            '''
            print(f"=== камень {i} ===")
            print(f"первый индекс {first}")
            print(f"последний индекс {last}")
            print(f"максимальная светимость {max_light} на снимке {max_light_index}")
            print(f"    с центром в {max_light_center[0]} {max_light_center[1]}")
            print(f"    с шириной {max_light_params[0]} и высотой {max_light_params[1]}")
            print()
            '''
        return res

    # define dir of output stones parameters
    def get_output_path():
        if not os.path.exists(stones_dir_path):
            os.makedirs(stones_dir_path)
            print('Создан каталог ', stones_dir_path)
        # else:
        #    shutil.rmtree(stones_dir_path)
        #    os.makedirs(stones_dir_path)
        return stones_dir_path

    # load array
    def load_numpy_array():
        return light_array

    def get_filename_param_csv():
        return filename_param_csv_path

    # get parameters of found stones
    def get_found_stones_param(index, kidney_position):
        if kidney_position == 'left':
            stone = left_stones_params[index]
        elif kidney_position == 'right':
            stone = right_stones_params[index]
        return stone.x_center, stone.z_center, stone.w, stone.h, stone.first_index, stone.last_index, stone.number

    # get parameters of numpy array
    def get_numpy_parameters(filename):
        if geometry_input is not None:
            return geometry_input['params']
        param_num = []
        fieldnames = ["Study Date",
                      "Series Description",
                      "Patient's Name",
                      "Patient ID",
                      "Spacing Between Slices",
                      "Series Number",
                      "Start Slice Location",
                      "End Slice Location",
                      "Slice Thickness",
                      "Rows",
                      "Columns",
                      "Samples per Pixel",
                      "Pixel Spacing X",
                      "Pixel Spacing Y",
                      "Rescale Intercept",
                      "Rescale Slope",
                      "Shape Z, Y, X",
                      "Z",
                      "Y",
                      "X"
                      ]

        with open(filename, newline='') as f:
            reader = csv.DictReader(f)
            for row in reader:
                for i in range(len(fieldnames)):
                    # print(row[fieldnames[i]])
                    param_num.append(row[fieldnames[i]])
        return param_num

    def calc_stone_parameters(index, kidney_position):
        global stone_param, index_kidney, norm_stone_image, frame_size_stone, ds_array, z_beg, z_end, start_slice, \
            end_slice, x_beg, x_end, med_slice, realLength_stone, realHeight_stone
        # filename_param_csv = get_filename_param_csv()
        param_numpy = get_numpy_parameters(get_filename_param_csv())
        ds_array = load_numpy_array()
        index_kidney = 'lk_' if kidney_position == 'left' else 'rk_'
        filename_stone_param = get_output_path() + 'stnpar_' + index_kidney + str(index) + '.txt'

        # StudyDate = param_numpy[0]
        # SeriesDescription = param_numpy[1]
        # PatientName = param_numpy[2]
        # PatientID = param_numpy[3]
        x_thin, y_thin, z_thin = legacy_spacing_xyz(param_numpy)
        # SeriesNumber = param_numpy[5]
        # StartSliceLocation = param_numpy[6]
        # EndSliceLocation = param_numpy[7]
        SliceThickness = float(param_numpy[8])
        # Rows = param_numpy[9]
        # Columns = param_numpy[10]
        # SamplesperPixel = param_numpy[11]
        # RescaleIntercept = param_numpy[14]
        # RescaleSlope = param_numpy[15]
        # ShapeZYX = param_numpy[16]
        z_ = int(param_numpy[17])
        # y_ = int(param_numpy[18])
        x_ = int(param_numpy[19])

        assembled = assembled_left[index] if kidney_position == 'left' else assembled_right[index]
        x_beg, start_slice, z_beg = map(int,assembled.lower_xyz)
        x_end, end_slice, z_end = map(int,assembled.upper_xyz)
        med_slice = (left_stones_params if kidney_position == 'left' else right_stones_params)[index].number
        med_slice = int(np.clip(med_slice,start_slice,end_slice-1))
        Length_stone = (x_end - x_beg)
        Height_stone = (z_end - z_beg)
        realLength_stone = Length_stone * x_thin
        realHeight_stone = Height_stone * z_thin
        x_frame = Length_stone
        y_frame = max(1,int(round(Length_stone * (realHeight_stone / realLength_stone))))
        frame_size_stone = (x_frame, y_frame)
        only_stone = assembled.values_zyx
        count_stone_vox = int(np.count_nonzero(only_stone))
        dp_stone = float("nan")
        or_stone = float("nan")
        dov_stone = float("nan")

        if np.any(only_stone != 0):
            max_HU = only_stone.max()
            min_HU = only_stone.max()
        else:
            max_HU = min_HU = 0

        # find min HU
        for z in range(only_stone.shape[0]):
            for y in range(only_stone.shape[1]):
                for x in range(only_stone.shape[2]):
                    if only_stone[z, y, x] != 0:
                        if only_stone[z, y, x] < min_HU:
                            min_HU = only_stone[z, y, x]

        # find average HU
        sum_HU = 0
        for z in range(only_stone.shape[0]):
            for y in range(only_stone.shape[1]):
                for x in range(only_stone.shape[2]):
                    if only_stone[z, y, x] != 0:
                        sum_HU += only_stone[z, y, x]
        ave_HU = (sum_HU / count_stone_vox) if count_stone_vox != 0 else 0

        # calc density of stones array
        dens_stone, dens_stone_sum = dens_stone_volume_from_hu(only_stone)

        Length_stone = (x_end - x_beg) * x_thin / 10
        Height_stone = (z_end - z_beg) * z_thin / 10

        # calc volume of stone
        volume_unit_vox = x_thin * y_thin * z_thin
        volume_unit_sm = volume_unit_vox / 1000
        volume_stone = volume_unit_sm * count_stone_vox
        mass_stone = volume_unit_sm * dens_stone_sum
        volume_stone1 = volume_unit_sm * dens_stone.shape[0] * dens_stone.shape[1] * dens_stone.shape[2]
        if count_stone_vox > 0:
            ave_dens = dens_stone_sum / count_stone_vox
        else:
            ave_dens = 0

        # stone_param = []
        stone_param = [index,  # 0
                       med_slice,  # 1
                       Length_stone,  # 2
                       Height_stone,  # 3
                       volume_stone1 / (Length_stone * Height_stone),  # 4
                       volume_unit_vox,  # 5
                       volume_unit_sm,  # 6
                       volume_stone1,  # 7
                       volume_stone,  # 8
                       count_stone_vox,  # 9
                       mass_stone,  # 10
                       ave_dens,  # 11
                       ave_dens * volume_stone,  # 12
                       max_HU,  # 13
                       min_HU,  # 14
                       ave_HU,  # 15
                       x_beg,  # 16
                       x_end,  # 17
                       z_beg,  # 18
                       z_end,  # 19
                       start_slice,  # 20
                       end_slice,  # 21
                       realLength_stone,  # 22
                       realHeight_stone,  # 23
                       frame_size_stone,    # 24
                       dp_stone,    # 25
                       or_stone,    # 26
                       dov_stone]  # 27

        # save param`s of stone into file
        with open(filename_stone_param, 'w', encoding='utf-8') as f:
            f.write(f'Параметры камня: \n')
            f.write(f'{list_text[19] if kidney_position == "right" else list_text[20]}, камень {index}, ')
            # f.write(f'срез {list_slices_of_stones_RK[index_of_stone] if kidney_key == "right" else list_slices_of_stones_LK[index_of_stone]}\n')
            f.write(
                f'размеры камня - {stone_param[2]:.2f} см Х {stone_param[3]:.2f} см Х {stone_param[4]:.2f} см \n')
            # s4 = f'объем 1 вокселя, мм3 - {stone_param[5]:.2f}, объем 1 вокселя, см3 - {stone_param[6]:.2f}\n'
            # s5 = f'объем пространства, см3 - {stone_param[7]:.2f}, объем камня, см3 - {stone_param[8]:.2f}\n'
            # s6 = f'количество вокселей в камне - {stone_param[9]}\n'
            f.write(f'масса камня, грамм -  {stone_param[10]:.2f}\n')
            f.write(f'средняя плотность, гр/см3 -  {stone_param[11]:.2f}\n')
            f.write(f'масса по средней плотности, грамм -  {stone_param[12]:.2f}\n')
            f.write(f'максимальная плотность по HU = {stone_param[13]}\n')
            f.write(f'минимальная плотность по HU = {stone_param[14]}\n')
            f.write(f'средняя плотность по HU = {stone_param[15]:.0f}\n')
            f.write(f'точность определения камня =не вычислено\n')
            f.write(f'достоверность определения камня =не вычислено\n')
            f.write(f'правдоподобие определения камня =не вычислено\n')
        f.close()

        # calc new reduced density of stones array
        new_dens_stone = new_dens_stone_from_density(dens_stone)

        np.save(get_output_path() + 'st_' + index_kidney + str(index), only_stone)

        # visualisation

        # plot slice stone image
        stone_image = ds_array[z_beg:z_end, med_slice, x_beg:x_end]
        norm_stone_image = cv2.resize(np.asarray(stone_image,dtype=np.float32), frame_size_stone)
        fig_slice = v3d.stone_slice_figure(
            norm_stone_image, med_slice, realLength_stone, realHeight_stone, index_kidney + str(index)
        )
        image_name = get_output_path() + 'stone_' + index_kidney + str(index) + '.png'
        fig_slice.savefig(image_name, bbox_inches='tight', transparent=True, format='png')
        plt.close(fig_slice)

        # plot 3D image of density of stones
        render_sx, render_sy, render_sz = legacy_spacing_xyz(param_numpy)
        if np.any(only_stone > 0):
            fig_vox1 = v3d.stone_vox3D_visualisation(
                cube_xyz(only_stone), render_sz, render_sx, render_sy
            )
            stone_image_name = get_output_path() + '/stone' + index_kidney + str(index) + '_1' + '.png'
            fig_vox1.savefig(stone_image_name, transparent=True, bbox_inches='tight', format='png')
            plt.close(fig_vox1)

        # plot 3D image of reduced density of stones
        if np.any(new_dens_stone > 0):
            fig_vox2 = v3d.stone_vox3D_visualisation(
                cube_xyz(new_dens_stone), render_sz, render_sx, render_sy, cmap=cm.Set1
            )
            stone_image_name = get_output_path() + '/stone' + index_kidney + str(index) + '_2' + '.png'
            fig_vox2.savefig(stone_image_name, transparent=True, bbox_inches='tight', format='png')
            plt.close(fig_vox2)

        # here you need to insert the output in PDF of the parameters of only current stone with pictures !!!!!!

        return stone_param

    def form_string(file_name):  # read text file with stone parameters
        text_param_stone = ''
        if Path(file_name).is_file():
            try:
                with open(file_name, "rt") as f:
                    text_param_stone = f.read()
            except Exception:
                text_param_stone = list_text[3]
        return text_param_stone

    input_path = str(input_path) + '/'
    labels_dir_path = input_path + 'detect/labels/'
    dp_kidney = float("nan")
    or_kidney = float("nan")
    dov_kidney = float("nan")

    # ID = str('/' + Path(input_path).parts[3][0:4])
    # ID = gi.patient_ID
    # filename_param_csv_path = input_path + ID + 'arrayinfo.csv'
    geometry_input=load_geometry_input(input_path)
    metadata=sorted(Path(input_path).glob('*arrayinfo.csv'))
    if geometry_input is None and not metadata:
        raise ValueError('Не найдены данные HU и геометрии серии. Выберите каталог detect корональных изображений из полного экспорта DICOM.')
    filename_param_csv_path = str(metadata[0]) if metadata else None
    param_numpy = get_numpy_parameters(get_filename_param_csv())

    labels_names = sorted(p.name for p in Path(labels_dir_path).glob("*.txt"))
    ID = param_numpy[3]
    SliceThickness = param_numpy[8]
    x_thin = param_numpy[12]
    y_thin = param_numpy[13]
    numpy_file_path = input_path + str(ID) + 'array.npy'
    light_array = (geometry_input['volume'] if geometry_input is not None else
                   np.load(numpy_file_path,mmap_mode='r',allow_pickle=False))
    parser = Parser()

    labels_list = sorted(map(lambda x: parser.parse(labels_dir_path, x), labels_names), key=lambda x: x.y)

    right_kidney_list = dict([(label.y, right_kidney_with_max_conf(label.slice_list)) for label in labels_list])
    left_kidney_list = dict([(label.y, left_kidney_with_max_conf(label.slice_list)) for label in labels_list])

    left_kidney_array = get_kidney_array(left_kidney_list, light_array)
    right_kidney_array = get_kidney_array(right_kidney_list, light_array)

    dp_kidney = float("nan")
    or_kidney = float("nan")
    dov_kidney = float("nan")

    all_stones = dict([(label.y, list(filter(Utils.is_stone, label.slice_list))) for label in labels_list])
    stones_with_right_kidney = dict(filter(lambda x: right_kidney_list[x[0]], all_stones.items()))
    stones_with_left_kidney = dict(filter(lambda x: left_kidney_list[x[0]], all_stones.items()))

    for i in stones_with_right_kidney:
        stones_with_right_kidney[i] = list(
            filter(lambda x: Utils.is_in_other_slice(x, right_kidney_list[i]), stones_with_right_kidney[i]))
    for i in stones_with_left_kidney:
        stones_with_left_kidney[i] = list(
            filter(lambda x: Utils.is_in_other_slice(x, left_kidney_list[i]), stones_with_left_kidney[i]))

    left_groups = stone_clusterize(stones_with_left_kidney)
    right_groups = stone_clusterize(stones_with_right_kidney)
    options = assembly_options()
    spacing = legacy_spacing_xyz(param_numpy)
    left_groups,assembled_left=consolidate_clusters(light_array,left_groups,spacing,**options)
    right_groups,assembled_right=consolidate_clusters(light_array,right_groups,spacing,**options)
    left_stones_params = stone_info(left_groups)
    right_stones_params = stone_info(right_groups)
    named_stones = ([(f'lk_{i}',s) for i,s in enumerate(assembled_left)] +
                    [(f'rk_{i}',s) for i,s in enumerate(assembled_right)])
    conflicts = assembly_conflicts(named_stones)
    if conflicts:
        details = '; '.join(f"{c['first']} / {c['second']}: {c['shared_voxels']} вокселей" for c in conflicts)
        raise ValueError('Неоднозначная принадлежность вокселей разным камням. '
                         'Проверьте детекции; результаты не сохранены. ' + details)
    result_directory = new_result_directory(input_path,output_dir)
    stones_dir_path = str(result_directory) + os.sep
    for name,values in [('LK',left_kidney_array),('RK',right_kidney_array)]:
        np.save(result_directory/(name+'.npy'),values)
        (result_directory/(name+'_param.txt')).write_text(
            'точность =не вычислено\nдостоверность =не вычислено\nправдоподобие =не вычислено\n',encoding='utf-8')
    assembly_report = dict(assembly_version=ASSEMBLY_VERSION,options=options,
        spacing_xyz_mm=list(spacing),conflicts=conflicts,
        stones=[dict(name=name,**stone.summary()) for name,stone in named_stones])
    (result_directory/'assembly.json').write_text(json.dumps(assembly_report,indent=2),encoding='utf-8')

    list_slices_of_stones_RK = []
    list_slices_of_stones_LK = []

    RS_params = []
    LS_params = []

    # calculation stones parameters (without GUI)
    # for right kidney
    for i in range(len(right_stones_params)):
        kidney_key = 'right'
        calc_stone_parameters(i, kidney_key)
        list_slices_of_stones_RK.append(str(stone_param[1]))
        RS_params.append(stone_param)

    # for left kidney
    for i in range(len(left_stones_params)):
        kidney_key = 'left'
        calc_stone_parameters(i, kidney_key)
        list_slices_of_stones_LK.append(str(stone_param[1]))
        LS_params.append(stone_param)

    plt.close('all')

    # PDF with stones parameters
    pdfFileName = pw.create_PDF(stones_dir_path, RS_params, LS_params, param_numpy,case_path=input_path)

    result = {
        'assembly_version': ASSEMBLY_VERSION,
        'assembly_options': options,
        "detection_signature": _detection_signature(input_path),
        "stones_dir_path": stones_dir_path,
        "RS_params": RS_params,
        "LS_params": LS_params,
        "param_numpy": param_numpy,
        "list_slices_of_stones_RK": list_slices_of_stones_RK,
        "list_slices_of_stones_LK": list_slices_of_stones_LK,
        "pdfFileName": pdfFileName,
    }

    # Кэшируем полный результат, чтобы можно было открыть
    # ранее рассчитанные параметры без повторного тяжёлого расчёта.
    try:
        with (result_directory/'result_cache.pkl').open('wb') as f:
            pickle.dump(result,f)
        if output_dir is None:
            cache_file = _cache_file_path(input_path)
            Path(cache_file).parent.mkdir(parents=True, exist_ok=True)
            temporary_cache = result_directory/'latest_cache.tmp'
            with temporary_cache.open('wb') as f:
                pickle.dump(result, f)
            os.replace(temporary_cache,cache_file)
    except Exception:
        pass

    return result
