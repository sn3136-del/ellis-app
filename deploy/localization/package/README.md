# Ellis 本地安装包 · Local installation package

T站签证信息库的本地化验收安装包。一条命令启动，浏览器打开即用，不需要 Node 或 Python。
The T-Station Visa Information Base, packaged for local acceptance. One command, then open a browser. No Node or Python needed.

## 要求 Requirements

Docker Desktop 4.x（含 Docker Compose）。Windows、macOS、Linux 均可。
Docker Desktop 4.x with Docker Compose. Windows, macOS or Linux.

## 启动 Start

在本文件夹内运行 From this folder:

    docker compose up --build

首次构建约 2 到 3 分钟。然后打开 First build takes 2 to 3 minutes. Then open:

- 查询与展示 Query tool and display pages: http://localhost:8080
- 质控后台 Quality console: http://localhost:8080/#ops
- 接口健康 API health: http://localhost:8000/health

停止 Stop: `Ctrl+C`，或 `docker compose down`。

## 内容 What is inside

| 文件夹 Folder | 内容 Contents |
|---|---|
| `backend/` | 后端服务源码与依赖 Backend service and its dependencies |
| `data/` | 人工核验事实、政策注记、参考数据 Verified facts, policy notes, reference data |
| `dist/` | 已构建的网页前端 Prebuilt web frontend |
| `snapshot/` | 线上数据库快照与运营修改记录 Database snapshot and operator overrides from the live system |

快照日期见 `snapshot/SNAPSHOT.txt`。The snapshot date is in `snapshot/SNAPSHOT.txt`.

## 密钥 Keys

不配置任何密钥时，系统提供快照内全部已回答线路与完整质控台，可离线验收。
Without any key the app serves every route in the snapshot and the full quality console, fully offline.

AI 问答、从未查询过的新线路、「从来源刷新」与「用 Ellis AI 新增线路」需要模型密钥。配置 `MOONSHOT_API_KEY` 即可启用：
The AI Q&A, first-time routes, "Refresh from source" and "Add with Ellis AI" need a model key. Set `MOONSHOT_API_KEY` to enable them:

    MOONSHOT_API_KEY=your-key docker compose up --build

## 质控台登录 Console access

质控台使用请求头认证，页面已内置验收用的运营账号（ellis-ops-a 与 ellis-ops-b，复核人须与修正人不同）。
The console authenticates by request header. The page ships with the two acceptance operator accounts, ellis-ops-a and ellis-ops-b; the reviewer must differ from the corrector.

## 数据在哪里 Where the data lives

`snapshot/ellis.db` 是 SQLite 数据库，`snapshot/operator_overrides.json` 是运营编辑。在质控台的每次修改都写回这个文件夹，重启不丢失。要恢复到出厂快照，用安装包内的原文件覆盖即可。
`snapshot/ellis.db` is the SQLite database and `snapshot/operator_overrides.json` holds operator edits. Every change made in the console is written back to this folder and survives restarts. To reset, overwrite both files with the originals from the zip.

## 配套文档 Documents

《后台操作手册》与《数据口径说明》随本包一同交付（PDF，中英双语）。
The Backend Operations Manual and the Data Caliber Specification ship alongside this package as bilingual PDFs.

Ellis Intelligence, Inc. · ellis-visa.com
