from PySide6.QtWidgets import QApplication

from locallens.ui.app_icon import application_icon, application_icon_path


def test_local_lens_icon_is_available_in_the_development_tree():
    assert application_icon_path().is_file()
    assert application_icon_path().name == "locallens.ico"


def test_local_lens_icon_loads_in_qt():
    application = QApplication.instance() or QApplication([])

    assert not application_icon().isNull()
    application.setWindowIcon(application_icon())
    assert not application.windowIcon().isNull()
