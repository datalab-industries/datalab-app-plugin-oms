from datalab_app_plugin_oms import __version__
from datalab_app_plugin_oms.blocks import ExampleDataBlock


def test_version():
    assert __version__


def test_example_data_block():
    block = ExampleDataBlock
    assert block.version == __version__
