# ADR 0002: LangGraph como integración opcional

Estado: aceptada.

El MVP implementa su máquina de estados y checkpoints explícitamente. LangGraph
queda como extra `agentarium[langgraph]`: hacerlo obligatorio aumentaría el
tamaño y la superficie de compatibilidad sin aportar una capacidad necesaria al
flujo vertical. El orquestador y el repositorio tienen límites claros para
incorporarlo posteriormente.
