"""User-supplied mode names for the existing PDF time-table colours.

These are colour annotations, not a separate clinical or fuzzy classifier.
The PDF contains two additional blue shades within the eight legend groups.
"""

MODE_LEGEND = (
    ((57, 73, 171), 'Режим для дробления осколков камней'),
    ((3, 155, 229), 'Режим работы для камней с низкой плотностью'),
    ((0, 172, 193), 'Режим работы при неудобных подходах инструмента'),
    ((0, 137, 123), 'Оптимальный режим работы'),
    ((67, 160, 71), 'Режим работы в зоне высокой плотности камня'),
    ((124, 179, 66), 'Режим работы с высоким риском нанесения травмы'),
    ((142, 36, 170), 'Малоэффективный режим работы'),
    ((216, 27, 96), 'Малоэффективный режим работы — не рекомендовано к применению'),
)

_MODE_BY_COLOUR = dict(MODE_LEGEND)
_MODE_BY_COLOUR[(30, 136, 229)] = _MODE_BY_COLOUR[(3, 155, 229)]
_MODE_BY_COLOUR[(94, 53, 177)] = _MODE_BY_COLOUR[(57, 73, 171)]


def pdf_cell_colour(value, bands):
    """Use the PDF's first inclusive interval, including shared boundaries."""
    return next(band for band in bands if band['min'] <= value <= band['max'])


def colour_mode(background):
    return _MODE_BY_COLOUR.get(tuple(background), 'Режим не указан в цветовой легенде')
