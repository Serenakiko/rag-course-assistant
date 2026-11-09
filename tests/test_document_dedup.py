"""文档内容去重回归：同名/近似内容不能重复生成知识点。"""
from __future__ import annotations

import unittest
from types import SimpleNamespace

from backend.services.document_dedup import find_duplicate, fingerprint_text


class DocumentDedupTests(unittest.TestCase):
    def test_fingerprint_ignores_spacing_but_preserves_punctuation(self):
        """排版空白可忽略，但公式与代码中的标点符号不能消失。"""
        left = fingerprint_text("进程是资源分配的基本单位。调度器负责选择就绪进程。")
        spaced = fingerprint_text(" 进程 是资源分配的基本单位。\n调度器负责选择就绪进程。 ")
        punctuation_changed = fingerprint_text(
            "进程 是资源分配的基本单位，调度器负责选择就绪进程！"
        )

        self.assertEqual(left.content_hash, spaced.content_hash)
        self.assertNotEqual(left.content_hash, punctuation_changed.content_hash)

    def test_find_duplicate_detects_small_content_change(self):
        """少量增删改仍应识别为重复资料，避免生成第二个知识点。"""
        baseline = (
            "计算机存储系统按容量、速度和价格形成层次：寄存器最快但容量最小，"
            "Cache 次之，主存容量更大，辅存容量最大但速度最慢。"
            "程序访问具有时间局部性和空间局部性，Cache 利用局部性提高平均访问速度。"
            "直接映射实现简单，但冲突较多；组相联映射在成本和冲突率之间折中。"
        ) * 8
        fingerprint = fingerprint_text(baseline)
        changed = fingerprint_text(baseline.replace("直接映射实现简单", "直接映射实现较简单", 1))

        existing = SimpleNamespace(
            id=1,
            filename="存储系统.md",
            status="ready",
            content_hash=changed.content_hash,
            content_simhash=changed.simhash,
            content_length=changed.length,
        )
        self.assertIs(find_duplicate(2, fingerprint, [existing]), existing)

    def test_find_duplicate_keeps_distinct_materials(self):
        """不同主题的资料不能被近似去重误删。"""
        process = fingerprint_text(
            "进程是程序的一次执行过程，操作系统通过调度器分配 CPU 时间。"
            "进程状态包括就绪、运行和阻塞，进程切换需要保存和恢复上下文。"
        )
        network = fingerprint_text(
            "TCP 通过三次握手建立连接，通过序列号、确认号和重传机制保证可靠传输。"
            "拥塞控制包括慢开始、拥塞避免、快重传和快恢复。"
        )

        existing = SimpleNamespace(
            id=1,
            filename="网络.md",
            status="ready",
            content_hash=network.content_hash,
            content_simhash=network.simhash,
            content_length=network.length,
        )
        self.assertIsNone(find_duplicate(2, process, [existing]))


if __name__ == "__main__":
    unittest.main()
