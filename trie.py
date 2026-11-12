import re


class TrieNode:
    def __init__(self):
        self.children = {}
        self.ids = set()


class Trie:
    def __init__(self):
        self.root = TrieNode()

    def insert(self, text, item_id):
        for word in re.split(r"[\W_]+", text.lower()):
            if not word:
                continue
            node = self.root
            for ch in word:
                node = node.children.setdefault(ch, TrieNode())
                node.ids.add(item_id)  # every prefix remembers the ID

    def search(self, prefix):
        node = self.root
        for ch in prefix.lower():
            if ch not in node.children:
                return set()
            node = node.children[ch]
        return node.ids
