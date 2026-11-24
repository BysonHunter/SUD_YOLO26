import yaml
# import PySimpleGUI as sg
from pathlib import PurePath, Path
# import kidney_main_GUI as km
from sud_yolo26.ui import messages as interface
languages = ["Русский", "English"]
img_formats = ['png', 'jpg']

color_themes = ["Black", "BlueMono", "BluePurple", "BrightColors", "BrownBlue",
                "Dark", "Dark2", "DarkAmber", "DarkBlack", "DarkBlue",
                "DarkBrown", "DarkGreen", "DarkPurple", "DarkRed", "DarkTanBlue", "DarkTeal", "Default",
                "Green", "GreenMono", "GreenTan", "HotDogStand", "Kayak", "LightBlue", "LightBrown",
                "LightGreen", "LightGrey", "LightPurple", "LightTeal", "LightYellow", "Material1", "Material2",
                "NeutralBlue", "Purple", "Reddit", "Reds", "SandyBeach", "SystemDefault", "SystemDefault1",
                "SystemDefaultForReal", "Tan", "TanBlue", "TealMono", "Topanga"]


from .storage import get_config_path, read_yaml, read_settings, save_settings

def set_language(lang_of_interface):  # set the language of interface
    global tooltips, buttons, list_text, checkbox, main_menu, window_heads, titles
    if lang_of_interface == 'Русский':
        tooltips = interface.RU_TOOLTIPS
        buttons = interface.RU_BUTTONS
        list_text = interface.RU_LIST_TEXT
        checkbox = interface.RU_CHECKBOX
        main_menu = interface.RU_MENU
        window_heads = interface.RU_WINDOW_HEADS
        titles = interface.RU_TITLES
    elif lang_of_interface == 'English':
        tooltips = interface.EN_TOOLTIPS
        buttons = interface.EN_BUTTONS
        list_text = interface.EN_LIST_TEXT
        checkbox = interface.EN_CHECKBOX
        main_menu = interface.EN_MENU
        window_heads = interface.EN_WINDOW_HEADS
        titles = interface.EN_TITLES
    return tooltips, buttons, list_text, checkbox, main_menu, window_heads, titles
