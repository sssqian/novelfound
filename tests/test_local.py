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
from novelfound.local_parse import (decode_text, detect_encoding, parse_book,  # noqa: E402
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

PNG_1PX = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000a49444154789c6360000002000100ffff03000006000557bfabd4000000"
    "0049454e44ae426082")


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


class TestEncoding(LocalTestCase):
    """编码识别。"""

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

    def test_volume_headers_recognised(self) -> None:
        text = "第一卷 少年\n内容一。\n第二卷 风起\n内容二。\n"
        chapters = split_txt_chapters(text)
        self.assertEqual([c["title"] for c in chapters], ["第一卷 少年", "第二卷 风起"])

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
