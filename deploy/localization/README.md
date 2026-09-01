# Ellis 本地化部署包 Localized deployment package

为验收方产研团队提供的本地化安装包。一次构建，浏览器打开即用。

A localized install for the acceptance team. Build once, open in a browser.

## 快速开始 Quickstart

1. 构建前端 Build the frontend

       npx vite build --config vite.web.config.mjs

2. 启动 Start

       docker compose -f deploy/localization/docker-compose.yml up --build

3. 打开 Open http://localhost:8080

## 数据 Data

数据集随镜像携带（data/ 目录内的校验事实与政策注记）。要带着完整线上答案库
做离线验收，把线上 ellis.db 快照放入卷中：

The verified facts and policy notes ship inside the image. To carry the full
online answer set for offline acceptance, load a database snapshot into the
volume:

    docker compose -f deploy/localization/docker-compose.yml cp ellis.db backend:/var/lib/ellis/ellis.db
    docker compose -f deploy/localization/docker-compose.yml restart backend

快照由 Ellis 随验收材料提供，或从线上导出。
The snapshot ships with the acceptance materials.

## 密钥 Keys

不配置任何密钥时，系统提供全部已回答线路与完整质控台。配置
MOONSHOT_API_KEY 后还能回答从未查询过的新线路。

Without keys the app serves every answered route and the full quality
console. With MOONSHOT_API_KEY it also answers first-time routes.
