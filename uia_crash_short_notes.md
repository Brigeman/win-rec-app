# Короткие замечания по падению приложения

## Главный диагноз (подтверждено `crash 6.log` + `lifecycle.jsonl`)

Падение в **in-process UIA** (`uiautomation` → `GetChildren` / access violation).
`try/except` это не ловит — native crash убивает весь процесс.

Типичная цепочка в `lifecycle.jsonl` (сессия `17b01bf9d5b1`):

```text
14:00:47  deferred_startup → uia_thread_start (delay 5s)
14:00:52  uia_initializer_ready → uia_tick tick=1 (com_gate held ~560ms)
14:00:54  uia_tick tick=2 …
```

`com_gate` **не спасает** от AV: потоки сериализованы, но UIA всё равно падает внутри `_scan_once`.

## Почему «куча процессов в трее»

В `startup.log` видно **несколько сессий за секунду** (двойной клик / автоперезапуск):

```text
14:02:58  pid=8380  tray_ready
14:03:02  pid=13080 main_enter + deferred_startup (параллельно!)
14:03:02  pid=9328  app_exit_duplicate_instance
```

Несколько процессов пишут в **одни и те же** `startup.log` / `lifecycle.jsonl` — строки перемешаны.

## UIA по умолчанию выключен (v0.1.10+)

В коде:

- по умолчанию: **title probe** (`desktop-title-probe`)
- UIA только явно: `WINREC_ENABLE_UIA=1`

В логах **не должно быть** `desktop-call-uia-probe` / `uia_thread_start`, если UIA не включали.

## Production-план

```text
main process     → UI, трей, запись, title+audio детекция
uia subprocess   → uiautomation (риск изолирован)
```

## Диагностика

Логи в `%LOCALAPPDATA%\win-rec-app\logs\`:

| Файл | Назначение |
|------|------------|
| `startup.log` | фазы запуска |
| `probe.log` | pycaw / loopback / title |
| `threads.log` | старт фоновых потоков |
| `lifecycle.jsonl` | все события JSON |
| `app.log` | общий журнал |
| `crash.log` | faulthandler |

Перед тестом:

```powershell
taskkill /F /IM win-rec-app.exe
Remove-Item "$env:LOCALAPPDATA\win-rec-app\logs\*" -ErrorAction SilentlyContinue
```
