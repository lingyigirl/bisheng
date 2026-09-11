import base64
import io
import re
import tempfile
import unittest
from unittest import TestCase

from PIL import Image

from bisheng.workflow.nodes.report.text_classification import Patterns, TextClassificationReport

sample_text = """
# Sample Report

## 二级标题

### 三级标题

#### 四级标题

##### 五级标题

###### 六级标题

####### 七级标题

This is a sample report with an image and a table.
![Sample Image](https://example.com/image.png)
https://example.com/data.xlsx
http://example.com/data.csv
http://example.com/image.jpg
http://example.com/image.png
http://example.com/image.bmp
http://example.com/image.gif
http://example.com/image.webp

/bisheng/tmp1.png?dadsaa=adas /bisheng/tmp2.png

你好吗？这是**重点**
你好？这也是__重点__
| Header 1 | Header 2 |
|----------|----------|
| Data 1   | Data **2**   |
"""


class TestDocxStringClassification(TestCase):
    def setUp(self):
        self.text_classification = TextClassificationReport("/tmp/bisheng")

    def test_patterns(self):
        for one in Patterns:
            print(f"\n\nPattern Name: {one.name}, Type: {one.resource_type}\n\n")
            match_text = []
            for match in re.finditer(one.pattern, sample_text, flags=one.flags):
                raw_text = match.group(0)
                match_text.append(raw_text)
            if not match_text:
                print(f"No matches found for pattern: {one.name}")
            else:
                print(f"Matches found for pattern: {match_text}")

    def test_text_classification(self):
        all_data = self.text_classification.get_all_classified_data(sample_text)
        print(all_data)


class TestDocxStringBase64Image(TestCase):
    def setUp(self):
        # static_resource_path must exist (asserted in __init__)
        self.text_classification = TextClassificationReport(tempfile.gettempdir())
        buf = io.BytesIO()
        Image.new("RGB", (4, 4), "red").save(buf, "PNG")
        self.png_b64 = base64.b64encode(buf.getvalue()).decode()

    def test_bare_data_uri_becomes_image_bytes(self):
        result = self.text_classification.get_all_classified_data(f"data:image/png;base64,{self.png_b64}")
        # a match at position 0 leaves an empty leading text item (shared helper behavior)
        items = [item for item in result if item["type"] != "text" or item["content"]]
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["type"], "image")
        self.assertIsInstance(items[0]["content"], bytes)
        self.assertTrue(items[0]["content"].startswith(b"\x89PNG"))

    def test_markdown_data_uri_becomes_image_bytes(self):
        result = self.text_classification.get_all_classified_data(f"![chart](data:image/png;base64,{self.png_b64})")
        items = [item for item in result if item["type"] != "text" or item["content"]]
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["type"], "image")
        self.assertIsInstance(items[0]["content"], bytes)

    def test_garbage_base64_falls_back_to_text(self):
        result = self.text_classification.get_all_classified_data("data:image/png;base64,A")
        self.assertEqual(result[0]["type"], "text")

    def test_data_uri_keeps_surrounding_text(self):
        result = self.text_classification.get_all_classified_data(f"before data:image/png;base64,{self.png_b64} after")
        self.assertEqual([item["type"] for item in result], ["text", "image", "text"])

    def test_long_slash_text_does_not_hang(self):
        # regression: http_image backtracked exponentially on slash-heavy text
        # without image extensions (hung the workflow worker on large cells)
        result = self.text_classification.get_all_classified_data("ab/" * 300 + "z")
        self.assertEqual(result[0]["type"], "text")

    def test_long_slash_cell_does_not_hang(self):
        # same pathological input inside a markdown table cell
        cell = "ab/" * 300 + "z"
        table = f"| col |\n|-----|\n| {cell} |"
        result = self.text_classification.get_all_classified_data(table)
        table_items = [item for item in result if item["type"] == "table"]
        self.assertEqual(len(table_items), 1)

    def test_base64_image_inside_table_cell(self):
        cell = f"![x](data:image/png;base64,{self.png_b64})"
        table = f"| col |\n|-----|\n| {cell} |"
        result = self.text_classification.get_all_classified_data(table)
        table_items = [item for item in result if item["type"] == "table"]
        self.assertEqual(len(table_items), 1)
        cell_data = table_items[0]["content"][1][0]  # row 2 (row 0 is the header)
        image_items = [item for item in cell_data if item["type"] == "image"]
        self.assertEqual(len(image_items), 1)
        self.assertIsInstance(image_items[0]["content"], bytes)


if __name__ == "__main__":
    unittest.main()
