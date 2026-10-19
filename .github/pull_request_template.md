## 关联规格

<!-- 必填：这个 PR 实现了哪份规格？没有规格就先补 specs/ 下的文档 -->
Spec: `specs/NNN-xxx/spec.md`

## 做了什么

<!-- 一两句话说清产出，不要罗列改了哪些文件 -->

## 怎么验证的

- [ ] `cd backend && go build ./...` 通过
- [ ] `cd backend && go vet ./...` 通过
- [ ] `cd backend && go test -race ./...` 通过
- [ ] 相关接口手工 curl 验证过（贴关键输出）

## 影响面

- [ ] 改动了共享目录（`internal/platform/`、`go.mod`、`package.json`、`deploy/`、`warehouse/ddl/`）
      → 已在群里提前告知
- [ ] 新增了数据库迁移 / Redis key / Kafka topic
- [ ] 修改了接口契约 → 已同步更新 `specs/` 里的 openapi
- [ ] 涉及十条铁律中的任何一条（身份来源、超卖、幂等、Agent 只读、密钥）

## 遗留问题 / 想请 reviewer 重点看的地方
