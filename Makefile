.PHONY: run install logs

run:
	python main.py

install:
	python -m venv venv
	./venv/bin/pip install -r requirements.txt
	sudo cp systemd/mtd-agent.service /etc/systemd/system/
	sudo systemctl daemon-reload
	sudo systemctl enable mtd-agent

logs:
	sudo journalctl -u mtd-agent -f
