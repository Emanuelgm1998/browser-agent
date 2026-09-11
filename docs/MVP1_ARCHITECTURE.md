# Browser Agent IA — MVP-1 Dual Brain

## Objetivo

Evolucionar el Browser Agent existente hacia un agente capaz de recibir
una tarea, obtener planificación mediante dos modelos externos,
solicitar aprobación humana y ejecutar el plan mediante el Browser Agent
local existente.

## Arquitectura

USER
  ↓
reasoning_loop.py
  ↓
ChatGPT
  ↓
Claude
  ↓
ChatGPT final
  ↓
Structured Plan
  ↓
Human Gate
  ↓
Qwen3
  ↓
agent_core.py
  ↓
Browser Use
  ↓
Playwright
  ↓
Chromium
  ↓
Internet

## Principios

- Reutilizar el executor existente.
- No destruir las correcciones CDP existentes.
- No eliminar action ACL.
- No eliminar JSON validation.
- No eliminar logs E2E.
- Mantener perfiles separados.
- Requerir aprobación humana para acciones sensibles.
- No automatizar CAPTCHA/2FA.
- No almacenar secretos en Git.

## Perfiles

browser_profile_chat/
- ChatGPT
- Claude

browser_profile_work/
- Gmail
- sitios de trabajo
- sitios objetivo

## Archivos previstos

chat_brain.py
reasoning_loop.py
email_codes.py
setup_cuentas.py

## Executor existente

agent_core.py

Debe continuar siendo el núcleo de ejecución web.

## Regression

example.com
python.org

## MVP-1 target

>= 7/10 E2E tasks exitosas.

## Estado

Setup inicial creado.
Implementación funcional pendiente de auditoría del executor existente.
