.PHONY: up down test

up:
	docker compose up --build -d

down:
	docker compose down

test:
	cd backend && python -m unittest discover -s tests -v
