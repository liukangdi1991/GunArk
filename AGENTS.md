# Agent Instructions

默认使用 Superpowers 工作流。

当用户没有明确禁止时，Codex 应自行判断是否需要使用合适的 Superpowers skill：

- 设计/重构方案使用 `Superpowers:writing-plans`
- 执行已有计划使用 `Superpowers:executing-plans`
- 排查问题使用 `Superpowers:systematic-debugging`
- 核心逻辑修改使用 `Superpowers:test-driven-development`
- 完成前校验使用 `Superpowers:verification-before-completion`
- 代码审查使用 `Superpowers:requesting-code-review`

涉及子 Agent 或并行 Agent 时，必须先获得用户明确授权。
