# ADR 0001: núcleo Python y UI React

Estado: aceptada.

Se usa FastAPI, Pydantic, SQLAlchemy/SQLite, AsyncIO y React sobre Vite/Vinext.
El stack funciona sin cloud, conserva contratos tipados y tiene soporte maduro
en Windows. SQLite evita operar un servicio adicional para el MVP.
