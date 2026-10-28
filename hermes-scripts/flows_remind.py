#!/usr/bin/env python3
# Обёртка для no-agent cron Hermes (раз в минуту): напоминания по запланированным точкам.
# Пустой вывод = ничего не отправлять. Токены не тратятся.
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import flows  # noqa: E402

sys.exit(flows.main(["remind"]))
