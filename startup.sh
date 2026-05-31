#!/bin/bash
# Этот скрипт прогоняется один раз при старте контейнера (см. crontab @reboot).
# Цикл по основным операциям — чтобы сразу после поднятия контейнер был
# в полезном состоянии, а не ждал ближайшего крона.

echo "[$(date)] Startup tasks..."

# 1. Обновить access-token (если истёк) — все последующие команды его используют.
/usr/local/bin/python -m hh_applicant_tool refresh-token

# 2. Поднять резюме в выдаче.
/usr/local/bin/python -m hh_applicant_tool update-resumes

# 3. Почистить отказы и заблокированных в чатах (без AI-ответов — просто
#    чтобы не тащить за собой мёртвые переписки в первую рассылку).
/usr/local/bin/python -m hh_applicant_tool reply-employers --delete-discarded --delete-blacklisted

# 4. Прогнать рассылку откликов с AI.
/usr/local/bin/python -m hh_applicant_tool apply-vacancies --use-ai --force-message --ai-filter light

echo "[$(date)] Startup tasks finished."
