from src.extractors.age_extractor import AgeExtractor


def test_age_from_xml_labels():
    xml = """<?xml version='1.0' encoding='UTF-8'?>
<hierarchy>
  <node class='android.view.View' content-desc='Age'>
    <node class='android.widget.TextView' text='25'/>
  </node>
</hierarchy>
"""
    ex = AgeExtractor()
    assert ex.extract(xml) == 25


def test_age_from_header_parsing():
    xml = """<?xml version='1.0' encoding='UTF-8'?>
<hierarchy>
  <node class='android.widget.TextView' text='Sarah' bounds='[100,200][300,250]'/>
  <node class='android.widget.TextView' text='27' bounds='[350,200][400,250]'/>
</hierarchy>
"""
    ex = AgeExtractor()
    assert ex.extract(xml) == 27
