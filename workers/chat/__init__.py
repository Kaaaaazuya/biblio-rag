"""チャット生成層のインターフェース契約。

開発/本番で実装を差し替えるための抽象。
- 開発: OllamaChatClient
- 本番: Bedrock等のChatClient実装
"""
