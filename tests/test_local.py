# -*- coding: utf-8 -*-
"""本地电子书（TXT / EPUB）单元测试：解析、入库、以及"真的能读出来"。

运行：
    python -m unittest discover -s tests -v
"""
from __future__ import annotations

import shutil
import sys
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from novelfound import localbooks as lb  # noqa: E402
from novelfound.local_parse import (declared_encoding, decode_text,  # noqa: E402
                                    detect_encoding, parse_book,
                                    parse_epub, parse_txt, read_chapter,
                                    read_epub_chapter, read_txt_chapter,
                                    split_txt_chapters)
from novelfound.localbooks import LocalBooks  # noqa: E402
from novelfound.models import Chapter  # noqa: E402
from novelfound.sources.local import LocalSource  # noqa: E402

TXT_UTF8 = """书名：测试小说
作者：某作者

第一章 起点
这是第一章的正文内容，足够长以便通过过滤。
第二行正文，讲一点剧情。

第二章 风起
第二章的正文内容，也足够长。
又一段内容。

第三章 落幕
第三章正文收尾。
"""

TXT_GBK = "第一章 开始\n正文第一段内容，中文编码测试。\n第二章 结束\n正文第二段内容。\n"

CHAP1_HTML = """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml"><head><title>第一章</title></head>
<body><h1>第一章 开端</h1>
<p>这是 EPUB 第一章的正文，内容足够长。</p>
<p>第二段正文，用于测试分段。</p>
</body></html>"""

CHAP2_HTML = """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml"><head><title>第二章</title></head>
<body><h2>第二章 继续</h2>
<p>这是 EPUB 第二章的正文，也足够长。</p>
</body></html>"""

CONTAINER_XML = """<?xml version="1.0"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles><rootfile full-path="OEBPS/content.opf"
     media-type="application/oebps-package+xml"/></rootfiles>
</container>"""

OPF_XML = """<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="2.0" unique-identifier="bookid">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:title>测试 EPUB 书名</dc:title>
    <dc:creator>EPUB 作者</dc:creator>
  </metadata>
  <manifest>
    <item id="c1" href="chap1.xhtml" media-type="application/xhtml+xml"/>
    <item id="c2" href="chap2.xhtml" media-type="application/xhtml+xml"/>
    <item id="cover" href="images/cover.png" media-type="image/png"
          properties="cover-image"/>
  </manifest>
  <spine><itemref idref="c1"/><itemref idref="c2"/></spine>
</package>"""

def make_png(width: int = 4, height: int = 4,
             rgb: tuple = (200, 80, 80)) -> bytes:
    """生成一张**合法**的小 PNG（不依赖 Pillow）。

    之前手写的那串十六进制 PNG 是坏的（libpng 报 `IDAT: incorrect data check`），
    字节断言照样通过、真正解码时才失败——所以这里用 zlib + CRC 现场拼一张。
    """
    import struct
    import zlib

    raw = b"".join(b"\x00" + bytes(rgb) * width for _ in range(height))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)   # 8bit RGB
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


PNG_1PX = make_png()


class LocalTestCase(unittest.TestCase):
    """每个用例一个独立目录（放在工作区内，受限环境也能写）。"""

    def setUp(self) -> None:
        self.dir = ROOT / "tests" / ".tmp" / f"local_{id(self)}"
        shutil.rmtree(self.dir, ignore_errors=True)
        self.dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        shutil.rmtree(self.dir, ignore_errors=True)

    # ---------------------------------------------------------------- 造文件
    def write_txt(self, text: str, name: str = "测试小说.txt",
                  encoding: str = "utf-8") -> Path:
        path = self.dir / name
        path.write_bytes(text.encode(encoding))
        return path

    def write_epub(self, name: str = "测试书.epub") -> Path:
        path = self.dir / name
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("mimetype", "application/epub+zip")
            archive.writestr("META-INF/container.xml", CONTAINER_XML)
            archive.writestr("OEBPS/content.opf", OPF_XML)
            archive.writestr("OEBPS/chap1.xhtml", CHAP1_HTML)
            archive.writestr("OEBPS/chap2.xhtml", CHAP2_HTML)
            archive.writestr("OEBPS/images/cover.png", PNG_1PX)
        return path


class TestPngFixture(LocalTestCase):
    """测试用的 PNG 必须是真能解码的（回归：曾经用了坏图，字节断言照样过）。"""

    def test_generated_png_is_valid(self) -> None:
        import struct

        data = make_png(3, 2)
        self.assertTrue(data.startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertEqual(data[-8:-4], b"IEND")
        # IHDR 里的宽高
        width, height = struct.unpack(">II", data[16:24])
        self.assertEqual((width, height), (3, 2))
        # CRC 校验（PNG 每个 chunk 都带 CRC）
        import zlib
        offset = 8
        while offset < len(data):
            length = struct.unpack(">I", data[offset:offset + 4])[0]
            tag = data[offset + 4:offset + 8]
            body = data[offset + 8:offset + 8 + length]
            crc = struct.unpack(">I", data[offset + 8 + length:offset + 12 + length])[0]
            self.assertEqual(crc, zlib.crc32(tag + body) & 0xFFFFFFFF,
                             f"{tag!r} chunk 的 CRC 不对")
            offset += 12 + length


class TestEncoding(LocalTestCase):
    """编码识别（含那个把 60% 章节解成乱码的截断误判）。"""

    def test_utf8(self) -> None:
        self.assertEqual(detect_encoding("中文内容".encode("utf-8")), "utf-8")

    def test_gbk(self) -> None:
        encoding = detect_encoding("中文内容，测试".encode("gbk"))
        self.assertIn(encoding, ("gb18030", "gbk"))

    def test_utf8_bom(self) -> None:
        self.assertEqual(detect_encoding("中文".encode("utf-8-sig")), "utf-8-sig")

    def test_decode_gbk_roundtrip(self) -> None:
        text, encoding = decode_text("第一章 你好".encode("gbk"))
        self.assertEqual(text, "第一章 你好")
        self.assertIn(encoding, ("gb18030", "gbk"))

    def test_utf8_not_misdetected_when_prefix_splits_a_char(self) -> None:
        """回归：中文 UTF-8 在 4KB 处被切断，不能误判成 GBK。

        真实案例：一本 EPUB 的 1408 个 HTML 全部是标准 UTF-8，
        按"截断前缀 + 严格解码"判断，只有 40% 能通过，其余被当成 GBK → 60% 章节乱码。
        """
        body = "这是第一章的正文内容，用于验证编码判定。" * 400      # 远超 4KB
        raw = body.encode("utf-8")
        # 先确认前提：4096 处确实把一个汉字切成了两半（老逻辑就是在这里翻车的）
        split_here = False
        try:
            raw[:4096].decode("utf-8")
        except UnicodeDecodeError:
            split_here = True
        self.assertTrue(split_here, "前提：4KB 处应恰好截断一个多字节字符")
        text, encoding = decode_text(raw)
        self.assertEqual(encoding, "utf-8")
        self.assertEqual(text, body)

    def test_all_prefix_lengths_decode_as_utf8(self) -> None:
        """任意长度前缀（模拟不同文件的截断点）都不能误判成 GBK。"""
        raw = "诡秘之主第一部小丑第一章绯红。" .encode("utf-8") * 500
        wrong = []
        for cut in (4096, 4097, 4098, 8191, 8192):
            encoding = detect_encoding(raw[:cut])
            if encoding != "utf-8":
                wrong.append((cut, encoding))
        self.assertEqual(wrong, [], f"这些截断点被误判：{wrong}")

    def test_declared_encoding_wins(self) -> None:
        """文件自己声明了编码就按它解（EPUB 的 HTML 声明 utf-8）。"""
        raw = '<?xml version="1.0" encoding="gbk"?><p>中文内容测试</p>'.encode("gbk")
        self.assertEqual(declared_encoding(raw), "gb18030")
        text, encoding = decode_text(raw, declared_encoding(raw))
        self.assertEqual(encoding, "gb18030")
        self.assertIn("中文内容测试", text)

    def test_gbk_file_still_detected_without_declaration(self) -> None:
        """没有声明时，纯 GBK 文件仍要认出来（别被 utf-8 误收）。"""
        raw = "第一章 开始\n正文内容全是中文，用于编码测试。\n".encode("gbk") * 200
        text, encoding = decode_text(raw)
        self.assertIn(encoding, ("gb18030", "gbk"))
        self.assertIn("正文内容全是中文", text)

    def test_declared_encoding_ignored_when_invalid(self) -> None:
        """声明写错了（说 utf-8 其实是 GBK）时，要退回按内容判断。"""
        raw = "第一章 内容".encode("gbk") * 300
        declared = "utf-8"                    # 谎报
        text, encoding = decode_text(raw, declared)
        self.assertIn("第一章", text)
        self.assertIn(encoding, ("gb18030", "gbk"))


class TestTxtChapters(LocalTestCase):
    """TXT 章节切分与读取。"""

    def test_split_finds_chapters(self) -> None:
        chapters = split_txt_chapters(TXT_UTF8)
        titles = [c["title"] for c in chapters]
        self.assertEqual(len(chapters), 3)
        self.assertEqual(titles, ["第一章 起点", "第二章 风起", "第三章 落幕"])

    def test_chapter_offsets_cover_text(self) -> None:
        chapters = split_txt_chapters(TXT_UTF8)
        # 开头那段"书名/作者"信息被丢掉（不当成一章），所以第一段从章节标记处开始
        self.assertEqual(TXT_UTF8[chapters[0]["start"]:], TXT_UTF8[chapters[0]["start"]:])
        self.assertTrue(TXT_UTF8[chapters[0]["start"]:].startswith("第一章 起点"))
        self.assertEqual(chapters[-1]["end"], len(TXT_UTF8))
        for before, after in zip(chapters, chapters[1:]):
            self.assertEqual(before["end"], after["start"])

    def test_read_chapter_paragraphs(self) -> None:
        chapters = split_txt_chapters(TXT_UTF8)
        paragraphs = read_txt_chapter(TXT_UTF8, chapters[1])
        joined = "".join(paragraphs)
        self.assertIn("第二章的正文内容", joined)
        self.assertIn("又一段内容", joined)
        self.assertNotIn("第一章的正文", joined)          # 只取本章
        self.assertFalse(any(p.startswith("第二章 风起") for p in paragraphs),
                         "章节标题行不应出现在正文里")

    def test_fallback_when_no_chapter_marks(self) -> None:
        """没有任何章节标记时也要能读：按长度切块。"""
        text = "只有流水账一样的正文。" * 2000
        chapters = split_txt_chapters(text)
        self.assertGreater(len(chapters), 1)
        self.assertTrue(all(c["title"] for c in chapters))
        self.assertEqual(chapters[0]["start"], 0)

    def test_volume_headers_become_groups(self) -> None:
        """`第X卷` 不再算一章，而是给后面的章节打分组。"""
        text = ("第一卷 少年\n第一章 起点\n正文一，足够长以便通过过滤。\n"
                "第二章 风起\n正文二，足够长以便通过过滤。\n"
                "第二卷 归途\n第三章 归来\n正文三，足够长以便通过过滤。\n")
        chapters = split_txt_chapters(text)
        self.assertEqual([c["title"] for c in chapters],
                         ["第一章 起点", "第二章 风起", "第三章 归来"])
        self.assertEqual([c["group"] for c in chapters],
                         ["第一卷 少年", "第一卷 少年", "第二卷 归途"])

    def test_volume_intro_is_not_lost(self) -> None:
        """卷标题后面若有卷首语，要归到下一章（不能丢内容）。"""
        text = ("第一章 起点\n正文一，足够长以便通过过滤。\n"
                "第二卷 归途\n这一卷讲他回家的故事，这里是卷首语。\n"
                "第二章 归来\n正文二，足够长以便通过过滤。\n")
        chapters = split_txt_chapters(text)
        self.assertEqual(chapters[1]["group"], "第二卷 归途")
        paragraphs = read_txt_chapter(text, chapters[1])
        joined = "".join(paragraphs)
        self.assertIn("卷首语", joined)
        self.assertNotIn("第二卷 归途", joined)      # 卷标题行本身不进正文

    def test_group_line_not_in_any_chapter(self) -> None:
        text = ("第一卷 少年\n第一章 起点\n正文一，足够长以便通过过滤。\n"
                "第二卷 归途\n第二章 归来\n正文二，足够长以便通过过滤。\n")
        chapters = split_txt_chapters(text)
        for chapter in chapters:
            body = "".join(read_txt_chapter(text, chapter))
            self.assertNotIn("第二卷 归途", body)
            self.assertNotIn("第一卷 少年", body)

    def test_parse_txt_metadata(self) -> None:
        """文件里写了"书名/作者"就用它，比文件名可靠。"""
        path = self.write_txt(TXT_UTF8, "斗罗大陆（精校版）.txt")
        data = parse_txt(path)
        self.assertEqual(data["title"], "测试小说")
        self.assertEqual(data["author"], "某作者")
        self.assertEqual(len(data["chapters"]), 3)
        self.assertGreater(data["char_count"], 100)

    def test_title_falls_back_to_filename(self) -> None:
        """没有书籍信息时用文件名，并去掉"（精校版）"这类后缀。"""
        path = self.write_txt("第一章 起点\n正文内容足够长。\n第二章 风起\n又一段正文。\n",
                              "斗罗大陆（精校版）.txt")
        data = parse_txt(path)
        self.assertEqual(data["title"], "斗罗大陆")
        self.assertEqual(data["author"], "")
        self.assertEqual([c["title"] for c in data["chapters"]],
                         ["第一章 起点", "第二章 风起"])

    def test_parse_txt_gbk_file(self) -> None:
        path = self.write_txt(TXT_GBK, "gbk小说.txt", encoding="gbk")
        data = parse_txt(path)
        self.assertEqual(len(data["chapters"]), 2)
        paragraphs = read_chapter(path, {**data, "format": "txt"}, data["chapters"][0])
        self.assertTrue(any("中文编码测试" in p for p in paragraphs),
                        f"GBK 文件没读对：{paragraphs}")


class TestEpub(LocalTestCase):
    """EPUB 解析与读取。"""

    def test_parse_metadata_and_spine(self) -> None:
        data = parse_epub(self.write_epub())
        self.assertEqual(data["title"], "测试 EPUB 书名")
        self.assertEqual(data["author"], "EPUB 作者")
        self.assertEqual(len(data["chapters"]), 2)
        self.assertEqual(data["chapters"][0]["title"], "第一章 开端")
        self.assertEqual(data["chapters"][1]["href"], "OEBPS/chap2.xhtml")

    def test_cover_extracted(self) -> None:
        data = parse_epub(self.write_epub())
        self.assertTrue(data["cover"].startswith(b"\x89PNG"))

    def test_read_chapter(self) -> None:
        path = self.write_epub()
        data = parse_epub(path)
        paragraphs = read_epub_chapter(path, data["chapters"][0])
        joined = "".join(paragraphs)
        self.assertIn("EPUB 第一章的正文", joined)
        self.assertIn("第二段正文", joined)
        self.assertFalse(any("第一章 开端" == p for p in paragraphs))

    def test_parse_book_dispatch(self) -> None:
        data = parse_book(self.write_epub())
        self.assertEqual(data["format"], "epub")
        self.assertEqual(parse_book(self.write_txt(TXT_UTF8))["format"], "txt")

    def test_bad_epub_raises(self) -> None:
        path = self.dir / "坏书.epub"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("readme.txt", "这不是电子书")
        with self.assertRaises(ValueError):
            parse_epub(path)


class TestLocalBooks(LocalTestCase):
    """本地书库：入库、去重、删除。"""

    def make_library(self) -> LocalBooks:
        return LocalBooks(path=self.dir / "books.json",
                          files_dir=self.dir / "files")

    def test_import_copies_file(self) -> None:
        source = self.write_txt(TXT_UTF8)
        library = self.make_library()
        record = library.import_file(source)
        self.assertEqual(record["format"], "txt")
        self.assertEqual(len(record["chapters"]), 3)
        copied = library.file_path(record)
        self.assertTrue(copied.is_file())
        self.assertNotEqual(copied, source)
        source.unlink()                       # 原文件删掉也不影响
        self.assertTrue(copied.is_file())

    def test_reimport_same_book_replaces(self) -> None:
        library = self.make_library()
        library.import_file(self.write_txt(TXT_UTF8, "同一本.txt"))
        library.import_file(self.write_txt(TXT_UTF8, "同一本2.txt"))
        self.assertEqual(library.count(), 1)   # 同名同作者 → 替换，不重复

    def test_remove_deletes_files(self) -> None:
        library = self.make_library()
        record = library.import_file(self.write_txt(TXT_UTF8))
        path = library.file_path(record)
        self.assertTrue(library.remove(record["id"]))
        self.assertEqual(library.count(), 0)
        self.assertFalse(path.exists())

    def test_persisted_and_reloaded(self) -> None:
        library = self.make_library()
        library.import_file(self.write_txt(TXT_UTF8))
        again = LocalBooks(path=self.dir / "books.json",
                           files_dir=self.dir / "files")
        self.assertEqual(again.count(), 1)
        self.assertEqual(again.all()[0]["title"], "测试小说")

    def test_unsupported_suffix(self) -> None:
        path = self.dir / "书.pdf"
        path.write_bytes(b"%PDF-1.4")
        with self.assertRaises(ValueError):
            self.make_library().import_file(path)


class TestEpubImages(LocalTestCase):
    """EPUB 插图：正文内嵌图片 + 插图清单。"""

    def write_epub_with_image(self, name: str = "插图版.epub") -> Path:
        """仿真实 EPUB 的目录结构：正文在 Text/，插图在 Images/，src 用 ../Images/。"""
        path = self.dir / name
        opf = """<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="2.0" unique-identifier="id">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:title>插图版测试书</dc:title></metadata>
  <manifest>
    <item id="c1" href="Text/chap1.xhtml" media-type="application/xhtml+xml"/>
    <item id="c2" href="Text/chap2.xhtml" media-type="application/xhtml+xml"/>
    <item id="i1" href="Images/C1.png" media-type="image/png"/>
    <item id="i2" href="Images/未被引用的插图.png" media-type="image/png"/>
  </manifest>
  <spine><itemref idref="c1"/><itemref idref="c2"/></spine>
</package>"""
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("mimetype", "application/epub+zip")
            archive.writestr("META-INF/container.xml", CONTAINER_XML)
            archive.writestr("OEBPS/content.opf", opf)
            archive.writestr("OEBPS/Text/chap1.xhtml", """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml"><body>
<div class="imgh"><img alt="alt" src="../Images/C1.png" width="140"/></div>
<h1>第一章 带图</h1>
<p>图片下面的正文内容，足够长。</p>
</body></html>""")
            archive.writestr("OEBPS/Text/chap2.xhtml", CHAP2_HTML)
            archive.writestr("OEBPS/Images/C1.png", PNG_1PX)
            archive.writestr("OEBPS/Images/未被引用的插图.png", PNG_1PX)
        return path

    def test_inline_image_kept_in_order(self) -> None:
        """正文里的图片要留在原位置，且整个内容只占 1 个字符（不打乱分页偏移）。"""
        from novelfound.local_parse import IMAGE_CHAR, read_epub_chapter_rich

        path = self.write_epub_with_image()
        data = parse_epub(path)
        paragraphs, images = read_epub_chapter_rich(path, data["chapters"][0])
        self.assertEqual(list(images.keys()), [0], "图片应插在第一段位置")
        self.assertEqual(paragraphs[0], IMAGE_CHAR)
        self.assertEqual(len(paragraphs[0]), 1)
        self.assertTrue(images[0].startswith(b"\x89PNG"))
        self.assertTrue(any("图片下面的正文" in p for p in paragraphs))
        # 位置：图片在标题之前
        self.assertLess(paragraphs.index(IMAGE_CHAR),
                        next(i for i, p in enumerate(paragraphs) if "图片下面的正文" in p))

    def test_chapter_without_image_has_empty_images(self) -> None:
        from novelfound.local_parse import read_epub_chapter_rich

        path = self.write_epub_with_image()
        data = parse_epub(path)
        paragraphs, images = read_epub_chapter_rich(path, data["chapters"][1])
        self.assertEqual(images, {})
        self.assertTrue(any("EPUB 第二章的正文" in p for p in paragraphs))

    def test_image_list_includes_unreferenced(self) -> None:
        """插图清单要包含"包里存在但正文没引用"的图（这正是看不到的那批）。"""
        data = parse_epub(self.write_epub_with_image())
        names = {item["name"] for item in data["images"]}
        self.assertIn("C1.png", names)
        self.assertIn("未被引用的插图.png", names)

    def test_image_list_has_real_sizes(self) -> None:
        """体积要取 zip 里的真实大小（回归：之前一律显示 0 B）。"""
        data = parse_epub(self.write_epub_with_image())
        sizes = {item["name"]: item["size"] for item in data["images"]}
        self.assertTrue(all(size > 0 for size in sizes.values()), str(sizes))

    def test_source_serves_images(self) -> None:
        library = LocalBooks(path=self.dir / "b.json", files_dir=self.dir / "f")
        record = library.import_file(self.write_epub_with_image())
        source = LocalSource(None, library)
        book = library.to_book(record)
        items = source.list_images(book)
        self.assertGreaterEqual(len(items), 2)
        data = source.image_bytes(book, items[0]["path"])
        self.assertTrue(data.startswith(b"\x89PNG"))
        # 正文里的图要能读出来
        detail = source.fetch_detail(book)
        content = source.fetch_chapter(book, detail.chapters[0])
        self.assertEqual(len(content.images), 1)
        self.assertEqual(content.paragraphs[list(content.images)[0]], "\ufffc")

    def test_local_source_not_cached(self) -> None:
        """本地书源标记为不可缓存（插图字节不该进 SQLite）。"""
        self.assertFalse(getattr(LocalSource, "cacheable", True))


class TestEpubToc(LocalTestCase):
    """EPUB 目录（NCX）：标题与"部/卷"分组。"""

    def write_epub_with_toc(self) -> Path:
        path = self.dir / "分卷书.epub"
        ncx = """<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE ncx PUBLIC "-//NISO//DTD ncx 2005-1//EN"
 "http://www.daisy.org/z3986/2005/ncx-2005-1.dtd">
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">
  <head><meta name="dtb:uid" content="x"/></head>
  <docTitle><text>分卷书</text></docTitle>
  <navMap>
    <navPoint id="n1"><navLabel><text>第一部 少年</text></navLabel>
      <content src="chap1.xhtml"/>
      <navPoint id="n1-1"><navLabel><text>第一章 起点</text></navLabel>
        <content src="chap1.xhtml"/></navPoint>
    </navPoint>
    <navPoint id="n2"><navLabel><text>第二部 归途</text></navLabel>
      <content src="chap2.xhtml"/>
      <navPoint id="n2-1"><navLabel><text>第二章 归来</text></navLabel>
        <content src="chap2.xhtml"/></navPoint>
    </navPoint>
  </navMap>
</ncx>"""
        opf = """<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="2.0" unique-identifier="id">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:title>分卷书</dc:title><dc:creator>某作者</dc:creator></metadata>
  <manifest>
    <item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>
    <item id="c1" href="chap1.xhtml" media-type="application/xhtml+xml"/>
    <item id="c2" href="chap2.xhtml" media-type="application/xhtml+xml"/>
  </manifest>
  <spine toc="ncx"><itemref idref="c1"/><itemref idref="c2"/></spine>
</package>"""
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("mimetype", "application/epub+zip")
            archive.writestr("META-INF/container.xml",
                             CONTAINER_XML.replace("OEBPS/content.opf",
                                                   "OEBPS/content.opf"))
            archive.writestr("OEBPS/content.opf", opf)
            archive.writestr("OEBPS/toc.ncx", ncx)
            archive.writestr("OEBPS/chap1.xhtml", CHAP1_HTML)
            archive.writestr("OEBPS/chap2.xhtml", CHAP2_HTML)
        return path

    def test_titles_come_from_toc(self) -> None:
        data = parse_epub(self.write_epub_with_toc())
        self.assertEqual(data["toc_source"], "ncx/nav")
        self.assertEqual([c["title"] for c in data["chapters"]],
                         ["第一章 起点", "第二章 归来"])

    def test_groups_from_toc_nesting(self) -> None:
        data = parse_epub(self.write_epub_with_toc())
        self.assertEqual([c["group"] for c in data["chapters"]],
                         ["第一部 少年", "第二部 归途"])

    def test_falls_back_to_html_heading_without_toc(self) -> None:
        data = parse_epub(self.write_epub())          # 没有 ncx/nav
        self.assertEqual(data["toc_source"], "")
        self.assertEqual(data["chapters"][0]["title"], "第一章 开端")
        self.assertEqual(data["chapters"][0]["group"], "")


class TestLocalSource(LocalTestCase):
    """本地书源：导入后要能搜到、能取目录、能读正文（端到端）。"""

    def setUp(self) -> None:
        super().setUp()
        self.library = LocalBooks(path=self.dir / "books.json",
                                  files_dir=self.dir / "files")
        self.source = LocalSource(None, self.library)

    def test_imported_book_is_searchable(self) -> None:
        self.library.import_file(self.write_txt(TXT_UTF8))
        books = self.source.search("测试")
        self.assertEqual(len(books), 1)
        self.assertEqual(books[0].source, lb.SOURCE_KEY)
        self.assertTrue(lb.is_local_url(books[0].url))
        self.assertEqual(self.source.search("不存在的书名"), [])

    def test_detail_lists_all_chapters(self) -> None:
        record = self.library.import_file(self.write_txt(TXT_UTF8))
        book = self.library.to_book(record)
        detail = self.source.fetch_detail(book)
        self.assertEqual(len(detail.chapters), 3)
        self.assertEqual(detail.chapters[0].display_title, "第一章 起点")
        self.assertEqual(detail.chapters[0].url, f"{book.url}/0")

    def test_read_every_chapter(self) -> None:
        """三章都要真的读出正文（不是只有目录）。"""
        record = self.library.import_file(self.write_txt(TXT_UTF8))
        book = self.library.to_book(record)
        detail = self.source.fetch_detail(book)
        expected = ["第一章的正文", "第二章的正文", "第三章正文"]
        for chapter, marker in zip(detail.chapters, expected):
            content = self.source.fetch_chapter(book, chapter)
            self.assertIn(marker, "".join(content.paragraphs),
                          f"{chapter.title} 没读到正文")
            self.assertEqual(content.url, chapter.url)

    def test_read_epub_book(self) -> None:
        record = self.library.import_file(self.write_epub())
        book = self.library.to_book(record)
        detail = self.source.fetch_detail(book)
        self.assertEqual(len(detail.chapters), 2)
        first = self.source.fetch_chapter(book, detail.chapters[0])
        self.assertIn("EPUB 第一章的正文", "".join(first.paragraphs))
        # 封面也带出来了
        self.assertTrue(self.source.cover_bytes(book).startswith(b"\x89PNG"))

    def test_chapter_by_index_fallback(self) -> None:
        """旧进度里 chapter.index 丢了，也要能靠 URL 末尾的序号找回来。"""
        record = self.library.import_file(self.write_txt(TXT_UTF8))
        book = self.library.to_book(record)
        chapter = Chapter(title="", url=f"{book.url}/2", index=-1)
        content = self.source.fetch_chapter(book, chapter)
        self.assertIn("第三章正文", "".join(content.paragraphs))

    def test_missing_book_gives_friendly_error(self) -> None:
        from novelfound.net import NovelError
        book = self.library.to_book({"id": "nope", "title": "不存在"})
        with self.assertRaises(NovelError):
            self.source.fetch_detail(book)

    def test_missing_file_gives_friendly_error(self) -> None:
        from novelfound.net import NovelError
        record = self.library.import_file(self.write_txt(TXT_UTF8))
        book = self.library.to_book(record)
        self.library.file_path(record).unlink()
        with self.assertRaises(NovelError):
            self.source.fetch_chapter(book, Chapter(title="第一章", url=f"{book.url}/0",
                                                    index=0))


class TestSourceRegistry(LocalTestCase):
    """本地书源要出现在书源列表里（且排在前面）。"""

    def test_build_sources_contains_local(self) -> None:
        from novelfound.config import AppConfig
        from novelfound.sources import build_sources

        config = AppConfig(path=self.dir / "config.json")
        sources = build_sources(config, None)
        self.assertEqual(sources[0].key, lb.SOURCE_KEY)
        self.assertIn(lb.SOURCE_KEY, [s.key for s in sources])

    def test_can_be_disabled(self) -> None:
        from novelfound.config import AppConfig
        from novelfound.sources import build_sources

        config = AppConfig(path=self.dir / "config.json")
        config.set_source_enabled(lb.SOURCE_KEY, False)
        self.assertNotIn(lb.SOURCE_KEY, [s.key for s in build_sources(config, None)])


if __name__ == "__main__":
    unittest.main(verbosity=2)
