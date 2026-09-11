# Qt 文本排版测量模型（实测，误差 0）

写这份是因为双页分页连续错了两轮：先用**绝对坐标**判断（结果一段一页），
再用 `line.height()` 累加（结果每页多塞 2~3 行、底部被裁）。
下面的数字是离屏打印每一行/每一块坐标量出来的，不是推测。

## 一、行步进 ≠ line.height()

段落格式用 `setLineHeight(line_height * 100, ProportionalHeight)`（默认 190%）。
此时：

| 量 | 值 | 说明 |
| --- | --- | --- |
| `QTextLine.height()` | 30（正文 19pt） | **只是文字本身的高度** |
| 相邻行 `y` 之差（真实步进） | **57** | = 30 × 1.9 |
| 章首标题（+4pt 加粗） | height 37 → 步进 70.3 | = 37 × 1.9 |

**规则**：页高累加必须用步进，不能用 `height()`。

```python
# 正确：相邻行取 y 差；块内最后一行用"块占位 - 行偏移"反推
def line_advance(block_layout, i, block_span):
    if i + 1 < block_layout.lineCount():
        return block_layout.lineAt(i + 1).y() - block_layout.lineAt(i).y()
    return block_span - block_layout.lineAt(i).y()
```

## 二、块占位与文档高度

- `layout.blockBoundingRect(block).height()` = `(n-1) × 步进 + 末行 height`，
  **比真实占位少一个 (步进 − height)**（正文就是少 27px）——不能直接当块高用。
- 块真实占位（不含自己的下间距）= `下一块顶部 − 本块顶部 − 本块 bottomMargin`；
  最后一个块用 `document.size().height() − documentMargin − 本块顶部` 反推。
- `document.size().height()` = **最后一行底部（含步进）+ 一个 documentMargin**；
  最后一块自己的 `bottomMargin` **不计入**文档高度。

## 三、一页能放多少

```
可用高度 = viewport().height() − 2 × documentMargin
换页条件：行底部(含步进) − 本页页顶 > 可用高度
```

正文用 `document().setDocumentMargin(26)` 控制内边距（**不是 CSS padding**），
这样"段落坐标 ↔ 滚动位置"才一一对应。

实测（1360×880 窗口、双页、每栏 417px）：33 页 **0 溢出**，
填充率 min 0.917 / avg 0.963；超长段落被正确拆到 19 页。

## 四、分页锚点

- 主锚点是 **字符偏移 `char_offset`**（整章显示文本的偏移），跨字号、跨单双页、
  跨窗口尺寸都稳定；`block_index` / `page_index` 只作兼容。
- 一章的第一段是**章首标题**（`set_content` 里 prepend），所以段落序号整体后移一位。
- `_page_offsets` 存每页起始字符偏移；一页 = 一个字符区间 → 切成
  `(段落号, 起, 止)` 片段；**续页片段不带首行缩进**（`continuation` 块格式）。

## 五、其它硬性事实

- 翻页模式下滚动条策略 `ScrollBarAlwaysOff`；`ScrollBarAsNeeded` 只作兜底
  （万一某一行比整页还高）。
- `_render_fragments()` 是"页面渲染"的唯一入口，测量与显示必须走同一个函数，
  否则测出来的高度和实际画出来的不是一回事。
- 窗口/字号变化后要重新分页（`_relayout_timer`，防抖 160ms），
  并停在原字符偏移上。

## 六、自检写法

- 页面是否溢出：`document().size().height() <= viewport().height() + 2`。
- 是否"只塞了一行"：填充率 = `(doc高 − 2×margin) / 可用高度`，应当 ≥ 0.8
  （最后一页是本章剩余内容，天然不满，要排除）。
- **先跑 `--quick` 再跑 `--ui`**；`--ui` 联网，真实章节偶尔只有 272 字，
  分页类断言要用**内容量确定的合成正文**（见 `scripts/harness.py` 的 `synthetic_chapter()`）。
