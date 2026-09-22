# 乐谱反复路径展开 API

使用 Python + FastAPI 实现的乐谱反复路径展开服务。提交**按小节排列的结构标记**，
服务按演奏语义生成**实际演奏的小节序列**，并为每一步保留**原始小节号**。

支持的结构：

- 成对反复 `|: … :|`（含曲首隐式反复、嵌套反复、`repeat_times` 多遍反复）；
- 第一/第二（及第三……）结尾（volta 房子），结尾数须与反复遍数一致；
- `D.C.` / `D.S.` `al Fine` 与 `al Coda`（含 Segno / Fine / To Coda / Coda）。

展开与对比：

- `POST /api/v1/expand` —— 一次性展开整曲；
- `POST /api/v1/expand-page` —— 无状态分页展开：首页携带 `page_size`，
  后续凭签名游标 `next_cursor` 续页，拼接结果与一次性展开**逐步一致**；
- `POST /api/v1/timeline` —— 在展开语义之上，按每小节的 `beats` 与
  `tempo_bpm` 把实际演奏路径换算为**毫秒时间轴**；
- `POST /api/v1/path-diff` —— 对比两份乐谱：各自展开后按**最小编辑代价**
  对齐两条实际演奏路径，返回逐项 `match` / `insert` / `delete`、
  首个差异位置、差异总数与两侧总时长差。

遇到**未闭合反复、非法跳转、非法节拍/速度、超过最大展开步数**等问题时，
返回带**小节定位**的 422 错误，且**不产出任何部分序列/时间轴/对比结果**。

## 一条命令启动

需要 Docker（含 Compose 插件）：

```bash
docker compose up --build
```

也可后台运行并查看日志：

```bash
docker compose up --build -d
docker compose logs -f
```

启动完成后访问：

- 服务地址与端口：<http://localhost:8000>
- 健康检查：<http://localhost:8000/health>
- 交互式 API 文档（Swagger UI）：<http://localhost:8000/docs>
- OpenAPI Schema：<http://localhost:8000/openapi.json>

容器内已按 `Dockerfile` 完成全部依赖装配（`pip install -r requirements.txt`），
由 uvicorn 直接监听 `0.0.0.0:8000`；compose 自带健康检查（轮询 `/health`），
`docker compose ps` 显示 `healthy` 即就绪。快速验证时间轴接口：

```bash
curl -s http://localhost:8000/api/v1/timeline \
  -H 'Content-Type: application/json' \
  -d '{"measures":[{"measure":1,"beats":4,"tempo_bpm":120},{"measure":2,"beats":4}]}'
# => {"steps":[...],"total_duration_ms":4000,"warnings":[]}
```

停止与清理：

```bash
docker compose down
```

本地无 Docker 时也可直接运行：

```bash
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

## 必要配置

全部通过环境变量配置，均有默认值（见 `docker-compose.yml`）：

| 环境变量 | 默认值 | 说明 |
| --- | --- | --- |
| `MAX_EXPANSION_STEPS` | `100000` | 服务端允许的最大展开步数硬上限；请求超过此值直接 422 |
| `DEFAULT_MAX_STEPS` | `10000` | 请求未指定 `max_steps` 时使用的默认值（不得大于硬上限） |
| `CURSOR_SECRET` | `insecure-dev-cursor-secret-change-me` | 分页游标 HMAC 签名密钥；**生产环境必须替换**为足够长的随机串，否则游标可被伪造 |
| `MAX_PAGE_SIZE` | `1000` | 分页接口单页最大步数；`page_size` 超过此值直接 422 |
| `MAX_DIFF_CELLS` | `4000000` | 路径对比接口的对齐规模上限（剥离公共前后缀后：左长度 × 右长度）；超过直接 422 |
| 端口 | `8000` | 容器固定监听 8000；如需改宿主机端口，修改 compose 的端口映射，如 `"9000:8000"` |

## 接口

### `POST /api/v1/expand` — 展开

请求体：

```json
{
  "max_steps": 10000,
  "measures": [
    {"measure": 1, "repeat_start": true},
    {"measure": 2},
    {"measure": 3, "ending_start": true, "ending_number": 1, "ending_stop": true, "repeat_end": true},
    {"measure": 4, "ending_start": true, "ending_number": 2, "ending_stop": true},
    {"measure": 5}
  ]
}
```

`measure` 为原始小节号（正整数、严格递增，可以不连续，如弱起/合并小节）。
标记字段（除 `measure` 外均可省略，默认 `false`/`null`）：

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `repeat_start` | bool | 左反复记号 `|:` |
| `repeat_end` | bool | 右反复记号 `:|` |
| `repeat_times` | int ≥2 | 反复段演奏遍数，写在 `|:` 或 `:|` 小节上，默认 2；有结尾时必须等于结尾数 |
| `ending_start` | bool | 结尾（房子）起始小节 |
| `ending_number` | int | 结尾序号：1 = 第一结尾，2 = 第二结尾…… |
| `ending_stop` | bool | 结尾结束小节；省略时以下一个结尾起始为界 |
| `segno` | bool | Segno 𝄋（D.S. 目标） |
| `coda` | bool | Coda 𝄌 目标 |
| `fine` | bool | Fine |
| `to_coda` | bool | To Coda |
| `navigation` | string | `dc` / `ds` / `dc_al_coda` / `ds_al_coda` / `dc_al_fine` / `ds_al_fine`（裸 `dc`/`ds` 语义同 al Fine） |

响应（上例：`1,2,3` 反复后走第二结尾）：

```json
{
  "sequence": [1, 2, 3, 1, 2, 4, 5],
  "steps": [
    {"index": 1, "measure": 1, "ending": null, "action": "continue"},
    {"index": 2, "measure": 2, "ending": null, "action": "continue"},
    {"index": 3, "measure": 3, "ending": 1, "action": "repeat_back"},
    {"index": 4, "measure": 1, "ending": null, "action": "continue"},
    {"index": 5, "measure": 2, "ending": null, "action": "continue"},
    {"index": 6, "measure": 4, "ending": 2, "action": "continue"},
    {"index": 7, "measure": 5, "ending": null, "action": "end"}
  ],
  "total_steps": 7,
  "warnings": []
}
```

`action` 取值：`continue`（继续）、`repeat_back`（`:|` 跳回段首）、
`repeat_finish`（反复段奏完）、`navigation_jump`（触发 D.C./D.S.）、
`to_coda_jump`（跳往 Coda）、`fine_end`（在 Fine 处结束）、`end`（全曲最后一步）。

错误响应（HTTP 422，`detail` 中每条错误都带 `code`、`message` 与可选的 `measure`）：

```json
{
  "detail": [
    {"code": "UNCLOSED_REPEAT", "message": "存在未闭合的左反复记号 |:（缺少配对的 :|）", "measure": 1}
  ]
}
```

### `POST /api/v1/expand-page` — 无状态分页展开

与 `/expand` 使用同一份 `measures`，按页返回演奏步骤。服务不保存任何会话：
续页所需的全部虚拟机状态（当前小节位置、嵌套反复各段的遍数计数、结尾选择、
D.C./D.S. 折返纪元、已产出步数）都打包进**签名游标** `next_cursor`，
续页时从断点直接继续（**不做从头重放**），因此全部页拼接后与一次性
`/expand` 的 `steps`（含 `index`、`ending`、`action`）**完全一致**。

**首页**：携带 `measures` 与 `page_size`（可选 `max_steps`，同 `/expand` 语义）：

```bash
curl -s http://localhost:8000/api/v1/expand-page \
  -H 'Content-Type: application/json' \
  -d '{
    "page_size": 3,
    "measures": [
      {"measure": 1, "repeat_start": true},
      {"measure": 2},
      {"measure": 3, "ending_start": true, "ending_number": 1, "ending_stop": true, "repeat_end": true},
      {"measure": 4, "ending_start": true, "ending_number": 2, "ending_stop": true},
      {"measure": 5}
    ]
  }'
```

```json
{
  "sequence": [1, 2, 3],
  "steps": [
    {"index": 1, "measure": 1, "ending": null, "action": "continue"},
    {"index": 2, "measure": 2, "ending": null, "action": "continue"},
    {"index": 3, "measure": 3, "ending": 1, "action": "repeat_back"}
  ],
  "done": false,
  "next_cursor": "eyJmcCI6Ij...省略...J9.4f8d...签名...",
  "warnings": []
}
```

**续页**：提交**与首页完全一致的 `measures`** 和上一页返回的 `next_cursor`
（`page_size` / `max_steps` 可省略，若给出则必须与首页一致），
直到 `done=true`（此时 `next_cursor` 为 `null`）：

```bash
curl -s http://localhost:8000/api/v1/expand-page \
  -H 'Content-Type: application/json' \
  -d '{
    "cursor": "eyJmcCI6Ij...省略...J9.4f8d...签名...",
    "measures": [
      {"measure": 1, "repeat_start": true},
      {"measure": 2},
      {"measure": 3, "ending_start": true, "ending_number": 1, "ending_stop": true, "repeat_end": true},
      {"measure": 4, "ending_start": true, "ending_number": 2, "ending_stop": true},
      {"measure": 5}
    ]
  }'
```

游标安全模型：游标由服务端密钥（`CURSOR_SECRET`）以 HMAC-SHA256 签名，
并绑定**乐谱内容指纹**与 **`page_size` / `max_steps`**。以下情况统一返回
带错误码的 422：

| code | 触发场景 |
| --- | --- |
| `CURSOR_INVALID` | 游标格式损坏、签名不符（篡改）或版本不支持 |
| `CURSOR_SCORE_MISMATCH` | 续页提交的 `measures` 与游标绑定的乐谱不一致（换谱） |
| `CURSOR_PARAM_MISMATCH` | 续页显式给出的 `page_size` / `max_steps` 与首页不一致 |
| `PAGE_SIZE_REQUIRED` | 首页未提供 `page_size` |
| `PAGE_SIZE_TOO_LARGE` | `page_size` 超过服务端上限 `MAX_PAGE_SIZE` |

其余错误（结构非法、`MAX_STEPS_EXCEEDED` 等）与 `/expand` 完全一致。


### `POST /api/v1/timeline` — 演奏时间轴

在 `/expand` 相同的结构校验与展开语义之上，把实际演奏路径换算为毫秒时间轴。
请求体在 `measures` 的每个小节上额外携带：

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `beats` | number > 0 | 该小节的拍数（**每小节必填**，可为小数，如 3.5） |
| `tempo_bpm` | number > 0 | 速度标记（拍/分钟）；**首小节必填**作为初始速度，之后省略表示沿用当前速度 |

速度语义：每当演奏路径经过带 `tempo_bpm` 的小节（包括反复、`D.C./D.S.`
折返后**再次经过**），该速度从该小节起重新生效；每步时长 =
`beats × 60000 / tempo_bpm` 毫秒。

```bash
curl -s http://localhost:8000/api/v1/timeline \
  -H 'Content-Type: application/json' \
  -d '{
    "measures": [
      {"measure": 1, "beats": 4, "tempo_bpm": 120, "repeat_start": true},
      {"measure": 2, "beats": 4},
      {"measure": 3, "beats": 2, "tempo_bpm": 90, "repeat_end": true}
    ]
  }'
```

响应按演奏步骤返回 `occurrence`（该小节第几次被演奏）、`measure`、
`start_ms`、`end_ms`、`tempo_bpm`（该步生效的速度），并给出
`total_duration_ms`：

```json
{
  "steps": [
    {"occurrence": 1, "measure": 1, "start_ms": 0, "end_ms": 2000, "tempo_bpm": 120.0},
    {"occurrence": 1, "measure": 2, "start_ms": 2000, "end_ms": 4000, "tempo_bpm": 120.0},
    {"occurrence": 1, "measure": 3, "start_ms": 4000, "end_ms": 5333, "tempo_bpm": 90.0},
    {"occurrence": 2, "measure": 1, "start_ms": 5333, "end_ms": 7333, "tempo_bpm": 120.0},
    {"occurrence": 2, "measure": 2, "start_ms": 7333, "end_ms": 9333, "tempo_bpm": 120.0},
    {"occurrence": 2, "measure": 3, "start_ms": 9333, "end_ms": 10667, "tempo_bpm": 90.0}
  ],
  "total_duration_ms": 10667,
  "warnings": []
}
```

精度约定：内部以精确分数累计时长，**不逐步取整**；只有输出
`start_ms` / `end_ms` / `total_duration_ms` 时才四舍五入到毫秒，
因此相邻步骤的边界始终首尾相接，总时长等于全程精确时长的一次性取整。

非法节拍或速度（缺失/非正数/非有限值）、首小节缺速度、结构非法、展开超限，
都返回与 `/expand` 相同格式的 422（`detail` 带 `code` 与 `measure`），
且**不产出任何部分时间轴**。新增错误码见下表。

### `POST /api/v1/path-diff` — 演奏路径对比

提交 `left` / `right` 两份带 `beats` / `tempo_bpm` 的乐谱（字段约定同
`/timeline`）与可选的 `max_steps`（对两侧同时生效），两侧分别按与
`/timeline` 完全相同的语义校验并展开，再以**最小编辑代价**对齐两条实际
演奏路径：

- 对齐键为**原始小节号**；`match` 代价 0，`insert` / `delete` 代价 1；
- 同一小节被反复演奏多次时，匹配按演奏顺序稳定成对（第 k 次对第 k 次）；
- **平局裁决**：先剥离两条路径的公共前缀/后缀（直接记为 `match`），
  其余位置相同代价时按 `match → delete → insert` 稳定择优，
  对齐结果确定、可复现。

```bash
curl -s http://localhost:8000/api/v1/path-diff \
  -H 'Content-Type: application/json' \
  -d '{
    "left": [
      {"measure": 1, "beats": 4, "tempo_bpm": 120, "repeat_start": true, "repeat_times": 3},
      {"measure": 2, "beats": 4, "repeat_end": true}
    ],
    "right": [
      {"measure": 1, "beats": 4, "tempo_bpm": 120, "repeat_start": true},
      {"measure": 2, "beats": 4, "repeat_end": true}
    ]
  }'
```

左侧反复 3 遍、右侧 2 遍，对齐结果中第三遍为 `delete`：

```json
{
  "ops": [
    {"op": "match", "left_measure": 1, "left_occurrence": 1, "right_measure": 1, "right_occurrence": 1},
    {"op": "match", "left_measure": 2, "left_occurrence": 1, "right_measure": 2, "right_occurrence": 1},
    {"op": "match", "left_measure": 1, "left_occurrence": 2, "right_measure": 1, "right_occurrence": 2},
    {"op": "match", "left_measure": 2, "left_occurrence": 2, "right_measure": 2, "right_occurrence": 2},
    {"op": "delete", "left_measure": 1, "left_occurrence": 3, "right_measure": null, "right_occurrence": null},
    {"op": "delete", "left_measure": 2, "left_occurrence": 3, "right_measure": null, "right_occurrence": null}
  ],
  "first_diff_index": 5,
  "total_differences": 2,
  "left_total_duration_ms": 12000,
  "right_total_duration_ms": 8000,
  "duration_diff_ms": 4000,
  "warnings": []
}
```

响应字段：

| 字段 | 含义 |
| --- | --- |
| `ops` | 按对齐顺序的逐项结果；`op` 为 `match`（两侧一致）/ `delete`（左侧特有）/ `insert`（右侧特有），每项携带相关侧的 `measure` 与 `occurrence`（不适用的侧为 `null`） |
| `first_diff_index` | `ops` 中首个非 `match` 项的位置（从 1 开始）；两条路径完全一致时为 `null` |
| `total_differences` | 差异总数（`insert` + `delete` 项数，即最小编辑代价） |
| `left_total_duration_ms` / `right_total_duration_ms` | 两侧全曲总时长（毫秒，与 `/timeline` 相同的精确累计 + 输出取整） |
| `duration_diff_ms` | 两侧总时长差（**左 - 右**，带符号） |
| `warnings` | 两侧结构警告，每条带 `side`（`left` / `right`）标明来源 |

任一侧的**结构、节拍速度或展开上限**无效时，返回 422 且**不产出任何
部分对比结果**；`detail` 中每条错误都带 `code`、`measure`（小节定位）
与 `side`（标明出错的一侧），两侧的错误会一次性合并返回：

```json
{
  "detail": [
    {"code": "INVALID_BEATS", "message": "beats 必须为正数：第 2 小节的 beats=0.0", "measure": 2, "side": "left"},
    {"code": "UNCLOSED_REPEAT", "message": "存在未闭合的左反复记号 |:（缺少配对的 :|）", "measure": 1, "side": "right"}
  ]
}
```

对齐规模（剥离公共前后缀后：左长度 × 右长度）超过服务端上限
`MAX_DIFF_CELLS` 时返回 422 `DIFF_TOO_LARGE`，同样不产出部分结果。

### `POST /api/v1/validate` — 结构校验

请求体与展开接口相同。无论是否合法都返回 **200**，由 `valid` 字段标识，
并一次性给出全部 `errors`、`warnings` 以及解析出的 `repeat_sections`（便于人工核对）：

```bash
curl -s http://localhost:8000/api/v1/validate \
  -H 'Content-Type: application/json' \
  -d '{"measures":[{"measure":1,"repeat_start":true},{"measure":2}]}'
```

```json
{
  "valid": false,
  "errors": [
    {"code": "UNCLOSED_REPEAT", "message": "存在未闭合的左反复记号 |:（缺少配对的 :|）", "measure": 1}
  ],
  "warnings": [],
  "repeat_sections": []
}
```

## 演奏语义约定

1. `:|` 使当前反复段再奏，直到奏满声明遍数；嵌套反复在外层第二遍中已“完成”，
   内层不再重复。
2. 结尾小节只在“结尾号 == 当前遍数”时演奏，其余跳过。非最后一个结尾的末尾
   必须有 `:|`；最后一个结尾后的 `:|` 可省略（反复段在该结尾末尾隐式闭合）。
3. 按常见演奏惯例，**D.C./D.S. 折返后不再重复此前已奏过的反复段**
   （直接走最后一个结尾）；Fine / To Coda 只在折返后的第二遍生效，
   首遍经过 Fine / To Coda 时忽略。
4. 一首乐曲至多一个 D.C./D.S. 指令、一个 Segno / Coda / Fine / To Coda 目标点；
   `al Fine` 必须有 Fine，`al Coda` 必须有 To Coda 与 Coda。
5. Fine / To Coda / D.C./D.S. 指令必须位于反复段与结尾之外（不能写在反复小节杠
   或房子内），以保证跳转无歧义。

## 主要错误码

| code | 触发场景 |
| --- | --- |
| `UNCLOSED_REPEAT` / `UNCLOSED_ENDING` | 未闭合的 `|:` / 结尾没有包围反复段 |
| `MISSING_END_REPEAT` | 非最后结尾末尾缺少 `:|` |
| `ENDING_NUMBER_SEQUENCE` / `ENDING_NUMBER_MISSING` | 结尾编号不连续 / 缺编号 |
| `REPEAT_TIMES_ENDING_MISMATCH` | 反复遍数与结尾数不一致 |
| `MARKER_ON_REPEAT_BAR` / `MARKER_INSIDE_ENDING` / `NAVIGATION_INSIDE_REPEAT` | 非法标记位置 |
| `MISSING_SEGNO` / `MISSING_FINE` / `MISSING_TO_CODA` / `MISSING_CODA` | 跳转目标缺失 |
| `SEGNO_AFTER_NAVIGATION` / `FINE_AFTER_NAVIGATION` / `CODA_BEFORE_NAVIGATION` | 非法跳转顺序 |
| `MULTIPLE_NAVIGATION` / `AMBIGUOUS_*` | 多个指令或多个同名目标点 |
| `MAX_STEPS_EXCEEDED` | 展开步数超过请求的 `max_steps`（错误带当前小节定位） |
| `MAX_STEPS_TOO_LARGE` | 请求的 `max_steps` 超过服务端硬上限 |
| `BEATS_MISSING` / `INVALID_BEATS` | 小节缺少 `beats` / `beats` 非正数或非有限值（时间轴与路径对比接口） |
| `TEMPO_MISSING` / `INVALID_TEMPO` | 首小节缺少 `tempo_bpm` / 速度标记非正数或非有限值（时间轴与路径对比接口） |
| `DIFF_TOO_LARGE` | 路径对比的对齐规模超过 `MAX_DIFF_CELLS`（仅路径对比接口） |
| `CURSOR_INVALID` / `CURSOR_SCORE_MISMATCH` / `CURSOR_PARAM_MISMATCH` | 分页游标被篡改 / 换谱 / 分页参数不一致 |
| `PAGE_SIZE_REQUIRED` / `PAGE_SIZE_TOO_LARGE` | 首页缺 `page_size` / 超过 `MAX_PAGE_SIZE` |

## 测试

```bash
pip install -r requirements.txt pytest httpx
pytest -q
```

## 项目结构

```
app/
  main.py       # FastAPI 路由：/api/v1/expand、/api/v1/expand-page、/api/v1/timeline、/api/v1/path-diff、/api/v1/validate、/health
  analyzer.py   # 静态结构分析：反复段 / 结尾 / 导航标记解析与校验
  expander.py   # 演奏语义虚拟机：生成实际小节序列（VMState 支持断点续页）
  timeline.py   # 时间轴换算：beats/tempo -> 毫秒（Fraction 精确累计，输出才取整）
  diff.py       # 路径对齐：最小编辑代价（match/insert/delete），平局按路径顺序稳定择优
  pagination.py # 分页游标：HMAC 签名、乐谱指纹与分页参数绑定
  models.py     # Pydantic 请求/响应模型
  config.py     # 环境变量配置
  errors.py     # 带小节定位的错误类型
tests/
  test_api.py
  test_diff.py
Dockerfile
docker-compose.yml
```
