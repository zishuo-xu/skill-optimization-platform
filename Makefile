PY := .venv/bin/python

.PHONY: setup seed demo start run test clean

setup:            ## 创建 venv 并安装依赖
	bash scripts/setup.sh

seed:             ## 初始化示例项目并运行基线
	$(PY) -m app.seed

demo:             ## 初始化示例项目并运行一次单轮优化
	$(PY) -m app.seed --with-optimize

start:            ## 一键启动（自动安装依赖、初始化示例、打开浏览器）
	./start.sh

run:              ## 启动 Web 服务（不初始化、不打开浏览器）
	$(PY) -m app.server

test:             ## 运行全部自动化验收用例
	$(PY) -m pytest -q

clean:            ## 删除本地数据（不可恢复）
	rm -rf data .pytest_cache
	find . -name '__pycache__' -type d -prune -exec rm -rf {} +
