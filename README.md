# Sabre Quote Agent MVP 0.18.1

Corrección de rutas IATA y protección del catálogo local.

## Cambios
- `eze mia`, `mad-mex`, `EZE/MIA`, etc. ahora se reconocen aunque los códigos estén en minúscula.
- se mantiene protección contra falsos positivos de palabras comunes;
- el paquete distribuible ya NO incluye `data/reference.db`, para no pisar el catálogo global del usuario en futuras actualizaciones;
- si `reference.db` no existe, la aplicación puede recrear seeds mínimos automáticamente, pero para cobertura global debe ejecutarse el importador.

## Después de instalar esta versión
Como la v0.18 anterior pudo haber reemplazado el catálogo global:

```powershell
python scripts/import_reference_data.py --refresh
```

Esto sólo hace falta una vez ahora. En futuras actualizaciones, mientras conserves la carpeta `data/`, el ZIP ya no volverá a reemplazar `reference.db`.

Workflow operativo de cotización.

## Flujo
```text
Cotizar
  -> Seleccionar
  -> Refrescar / Reprice
  -> Comparar cambios
  -> Generar WhatsApp / Email
  -> Marcar enviada
```

## Nuevos datos persistidos
Cada cotización puede guardar:
- cliente
- referencia/expediente
- notas internas
- fecha de envío
- cotización padre
- cotización refrescada/hija

La migración de `data/quotes.db` es automática.

## Refresh / Reprice
`POST /quotes/{quote_id}/refresh`

Vuelve a ejecutar exactamente el `search_request` persistido contra Sabre, crea una cotización nueva y conserva la anterior.

La respuesta compara las opciones seleccionadas (o todas si todavía no hay selección):
- mismo itinerario / no disponible
- mismo producto tarifario
- precio anterior
- precio nuevo
- diferencia
- branded fare ya no disponible

La cotización anterior queda `superseded` y enlazada a la nueva.

## Workflow
`PATCH /quotes/{quote_id}/workflow`

Estados:
- active
- selected
- ready
- sent
- superseded

## Web UI
La interfaz agrega:
- Cliente
- Referencia
- Notas internas
- Guardar datos
- Refrescar / Reprice
- Marcar enviada
- Comparación visual del refresh

Air Rules sigue independiente y pendiente de la confirmación de Sabre.

Multi-cabin shopping + mixed-cabin safety.

## Multi-cabin
El parser ahora distingue tres casos:

1. **Cabina explícita única**
   - `premium economy` → sólo PREMIUM_ECONOMY.
   - `business` → sólo BUSINESS.

2. **Varias cabinas explícitas**
   - `economy, premium economy y business` → se ejecutan tres búsquedas BFM y se fusionan por itinerario.

3. **No se indicó cabina**
   - se ejecutan por defecto ECONOMY + PREMIUM_ECONOMY + BUSINESS.
   - FIRST sólo se consulta si se pide explícitamente.

El backend no intenta meter varias cabinas en una sola preferencia global. Hace fan-out por cabin y fusiona únicamente vuelos con firma exacta; también conserva itinerarios que sólo aparecen en una de las cabinas.

Esto permite que una opción muestre una escalera comercial real de Economy / Premium Economy / Business cuando Sabre devuelve esas cabinas.

## Mixed cabin por tramo
`ida Business, vuelta Premium Economy` se detecta de forma explícita:

```text
outbound_cabin = BUSINESS
return_cabin = PREMIUM_ECONOMY
```

La búsqueda NO se ejecuta todavía, porque el `CabinPref` de nuestra implementación BFM es global y no queremos convertir ese pedido en una tarifa de una sola cabina ni sumar dos one-way como si fueran el mismo ticket.

La Web UI devuelve la interpretación y una advertencia, con `quote=null`.

Esto es deliberado hasta validar con Sabre un mecanismo correcto para mixed cabin por leg.

## UI
La tarjeta de Interpretación ahora muestra también `Cabinas`.

## Refundable
La lógica estricta de 0.17.3.2 se mantiene:
- sólo sobreviven productos con refund branded explícitamente permitido o con cargo;
- `nonRefundable=false` por sí solo no alcanza.

Preferencia estricta de tarifas reembolsables.

Novedades:
- `con devolución`, `con reembolso`, `reembolsable`, `refundable` y frases equivalentes se interpretan como `fare_preference=refundable`.
- BFM se pide con Multiple Branded Fares + ReturnBrandAncillaries para obtener atributos de refund.
- Después de normalizar, el backend conserva únicamente productos cuya branded fare informa explícitamente devolución permitida (`F`) o con cargo (`C`).
- `nonRefundable=false` NO alcanza para considerar una tarifa reembolsable; se mantiene la política conservadora de v0.16.
- Si una opción sólo contiene productos con devolución no permitida o desconocida, esa opción se elimina.
- Si ninguna opción cumple, el resultado es 0 en vez de mostrar una tarifa contraria al pedido.
- Al usar `refundable`, no se agrega automáticamente Business como companion a una búsqueda Premium Economy.

Ejemplo:

```text
Cotizame EZE a Ljubljana del 19 al 30 de septiembre,
USD, en premium economy, con devolucion
```

Interpretación esperada:

```text
Cabina: PREMIUM_ECONOMY
Tarifa: refundable
```

Y sólo deben mostrarse branded fares con refund explícitamente permitido o permitido con cargo.

Corrección de falsos positivos con catálogo global:
- códigos IATA de aerolínea sólo se reconocen como código cuando aparecen explícitamente en mayúsculas;
- códigos IATA/city code de 3 letras también se separan de aliases naturales;
- palabras comunes como `al`, `de`, `la`, etc. ya no pueden convertirse accidentalmente en aerolíneas/aeropuertos;
- nombres completos como `Ljubljana`, `Turkish`, `Air Europa` siguen resolviéndose vía `reference.db`;
- no modifica BFM ni la Web UI.

Caso de regresión cubierto:

```text
Cotizame EZE a Ljubljana del 19 al 30 de septiembre, USD
→ EZE -> LJU
→ carriers=[]
→ sin warning de más de dos aeropuertos
```

Full Reference Catalog.

## Objetivo
El parser ya no necesita que agreguemos manualmente cada aeropuerto o aerolínea.

La fuente operativa sigue siendo:

```text
data/reference.db
```

pero ahora se puede poblar masivamente con:

```powershell
python scripts/import_reference_data.py --refresh
```

### Aeropuertos
El importador usa por defecto el CSV público de OurAirports:

```text
https://ourairports.com/data/airports.csv
```

Se importan únicamente filas que tengan código IATA. Se guardan:
- IATA
- nombre
- municipio
- país
- ICAO cuando está disponible
- latitud/longitud
- aliases de nombre
- municipio como alias sólo cuando resuelve a un único aeropuerto IATA
- keywords del dataset

OurAirports publica este dataset como Public Domain y lo actualiza cada noche.

### Aerolíneas
Mientras Sabre Airline Lookup no esté habilitado para RY3A, el importador soporta opcionalmente `airlines.dat` de OpenFlights:

```text
https://raw.githubusercontent.com/jpatokal/openflights/master/data/airlines.dat
```

Por defecto se cargan sólo aerolíneas marcadas activas. Esta fuente se trata como complementaria y puede estar menos actualizada que OurAirports; los seeds comerciales del proyecto permanecen como fallback.

También podés importar archivos descargados previamente:

```powershell
python scripts/import_reference_data.py `
  --airports-file C:\temp\airports.csv `
  --airlines-file C:\temp\airlines.dat `
  --refresh
```

## Multi-airport cities
Los city codes metropolitanos importantes siguen modelados explícitamente y no se inventan a partir del municipio:

```text
BUE -> EZE/AEP
SAO -> GRU/CGH/VCP
RIO -> GIG/SDU
NYC -> JFK/LGA/EWR
LON -> LHR/LGW
PAR -> CDG/ORY
ROM -> FCO/CIA
MIL -> MXP/LIN
TYO -> NRT/HND
OSA -> KIX/ITM
CHI -> ORD/MDW
WAS -> IAD/DCA/BWI
YTO -> YYZ/YTZ
BJS -> PEK/PKX
SHA -> PVG/SHA
SEL -> ICN/GMP
STO -> ARN/BMA
```

## Diagnóstico

```text
GET /reference/stats
GET /reference/resolve?q=Turkish&type=airline
GET /reference/resolve?q=Guarulhos&type=airport
```

Después de importar, reiniciar FastAPI no es obligatorio: el parser consulta SQLite local en cada resolución.

## Sabre Reference Data
`test_reference_data.py` sigue disponible. En las pruebas de RY3A CERT:
- Multi-Airport City Lookup: HTTP 403
- Airports at City: HTTP 403
- Airline lookup histórico: HTTP 404

Por eso Sabre queda como futura fuente opcional de sincronización y no como dependencia de cada cotización.

Reference Data Layer.

Objetivo: dejar de depender de listas hardcodeadas de aeropuertos y aerolíneas.

## Arquitectura
- `data/reference.db` guarda airports, multi-airport cities, airlines y aliases.
- El parser consulta `reference.db` antes de depender de los aliases históricos.
- El catálogo se puede ampliar sin modificar `agent_parser.py`.
- Hay seeds locales para que el sistema funcione aunque Sabre Reference Data no esté habilitado.
- Nuevo `scripts/test_reference_data.py` prueba endpoints de Sabre en CERT y, con `--sync`, persiste respuestas reconocibles.

## Sabre endpoints bajo prueba
Sabre todavía publica el spec oficial `multiairportcitylookupv1.yaml` para Multi-Airport City Lookup.
También probamos, de forma diagnóstica, endpoints históricos de la misma familia:

```text
/v1/lists/supported/cities
/v1/lists/supported/cities/SAO/airports/
/v1/lists/utilities/airlines/
```

El tercero se trata como `legacy-candidate`: no asumimos que siga habilitado hasta probarlo en CERT.

## Primera prueba

```powershell
python scripts/test_reference_data.py --env cert
```

Si los endpoints responden bien:

```powershell
python scripts/test_reference_data.py --env cert --sync
```

Esto alimenta:

```text
data/reference.db
```

y el parser puede reconocer nuevos códigos/nombres sin agregar código.

Ejemplos ya cubiertos por la capa local:

```text
Turkish        -> TK
Air Europa     -> UX
Gol            -> G3
San Pablo      -> SAO
São Paulo      -> SAO
Guarulhos      -> GRU
New York       -> NYC
London         -> LON
```

## Nota operativa
No hacemos llamadas de Reference Data en cada cotización. El parser lee SQLite local; Sabre se usa para refrescar el catálogo bajo demanda. Esto mantiene la búsqueda rápida y evita depender de un lookup externo para cada prompt.

Corrección del parser de aerolíneas en lenguaje natural:
- agrega G3 / Gol / Gol Linhas Aereas;
- reconoce `no cotizar`, `no incluir`, `evitar`, `excepto`, `menos`, `sin`;
- `no cotizar LATAM` produce `excluded_carriers=["LA"]`;
- `solo con Gol Linhas Aereas (G3)` produce `carriers=["G3"]`;
- `con G3` produce `carriers=["G3"]`;
- evita detectar accidentalmente `LA` como la palabra española "la"; el código LA sigue funcionando cuando se escribe explícitamente en mayúsculas.
- No modifica BFM, UI, persistencia ni Air Rules.

Primera Web UI del MVP.

## Qué mantiene de Air Rules
- Se conserva completo el contract probe de 0.16.2.
- `OTA_AirRulesLLSRQ` sigue sin transmitir nada hasta tener endpoint/action/session confirmados.
- `scripts/test_fare_rules.py` y `scripts/test_air_rules_contract.py` siguen disponibles.
- La Web UI puede mostrar la auditoría actual de `/fare-rules`, incluyendo qué conceptos requieren lookup externo.

## Nueva Web UI
Levantar:

```powershell
uvicorn app.main:app --reload
```

Abrir:

```text
http://127.0.0.1:8000/
```

o directamente:

```text
http://127.0.0.1:8000/app
```

La interfaz permite:
- escribir el pedido en lenguaje natural;
- elegir CERT/PROD;
- ejecutar `/agent/quote`;
- revisar la interpretación;
- ver vuelos y familias tarifarias;
- seleccionar uno o más ranks;
- persistir la selección;
- generar y copiar WhatsApp;
- previsualizar Email HTML;
- auditar reglas tarifarias;
- abrir cotizaciones anteriores desde el historial.

No usa React/Vue ni dependencias externas: es HTML/CSS/JS servido por el mismo FastAPI, para mantener el MVP simple y fácil de desplegar.

Swagger sigue disponible en:

```text
http://127.0.0.1:8000/docs
```

Contract probe para OTA_AirRulesLLSRQ.

Cambios:
- `scripts/test_fare_rules.py` usa correctamente `data/quotes.db` por defecto.
- Nuevo `scripts/test_air_rules_contract.py`.
- Valida que los fare components tengan origin/destination/fare basis/governing carrier.
- Comprueba por separado tres requisitos SOAP: endpoint, service action y Session Token.
- Nunca persiste el Session Token.
- `--execute` permanece bloqueado hasta tener contrato SOAP explícito.
- Referencias oficiales: SabreDevStudio `OTA_AirRulesLLSRQ-v2.3.0` y `SessionCreateRQ-v1.0.0`.

Prueba:

```powershell
python scripts/test_air_rules_contract.py `
  --quote-id Q-20260815-2D3A9D74 `
  --rank 1
```

Cuando Sabre confirme los datos:

```powershell
$env:SABRE_SOAP_ENDPOINT="..."
$env:SABRE_AIR_RULES_ACTION="..."
$env:SABRE_SOAP_SESSION_TOKEN="..."

python scripts/test_air_rules_contract.py `
  --quote-id Q-20260815-2D3A9D74 `
  --rank 1 `
  --execute
```

La transmisión sigue bloqueada en 0.16.2; el objetivo es comprobar que el contrato está completo antes de construir el envelope final.

Investigación técnica de Fare Rules / Category 16.

Esta versión es deliberadamente diagnóstica:
- conserva más metadata de `FareComponentDesc` en el normalizer (`component_ref`, governing carrier, vendor, tariff, rule number, amount/currency cuando Sabre los entregue);
- agrega `scripts/test_fare_rules.py`;
- extrae los fare components de una cotización persistida;
- genera `output/fare_rule_components.json`;
- genera `output/fare_rules_diagnostics.json`;
- genera `output/ota_air_rules_request.xml` como **artefacto diagnóstico NO transmisible**;
- `--execute` está bloqueado a propósito hasta validar con Sabre el contrato SOAP exacto (endpoint, namespaces/schema, SOAPAction y sesión/contexto).

Prueba recomendada:

```powershell
python scripts/test_fare_rules.py `
  --quote-id Q-20260815-EF550CCD `
  --rank 1 `
  --category 16 `
  --dry-run
```

Importante: las cotizaciones creadas antes de 0.16.1 pueden no contener los nuevos campos de FareComponentDesc porque el normalizer anterior no los persistía. Si el diagnóstico muestra campos faltantes, generá una cotización nueva con 0.16.1 y repetí la prueba.

No hace falta borrar `quotes.db`.

Novedades 0.16 — confiabilidad de reglas tarifarias:
- Nueva capa de auditoría de fare rules sobre la información que ya devuelve BFM.
- `GET /quotes/{quote_id}/fare-rules` devuelve, por producto, equipaje, cambios, devoluciones y ticketing con:
  - `status`
  - `source`
  - `confidence`
  - texto explicativo.
- Si BFM no trae una regla explícita, el estado queda `unknown`.
- `nonRefundable=true` se mantiene como evidencia fuerte de no reembolsabilidad.
- `nonRefundable=false` ya NO se traduce como “reembolsable”: se marca como información insuficiente y se pide confirmar fare rules.
- WhatsApp/email también adoptan esta política conservadora.
- `requires_external_rule_lookup=true` indica que para cambios/devoluciones faltaría consultar un servicio específico de reglas/revalidación.
- No se agregó todavía una llamada externa de Air Rules/Revalidate: primero validamos acceso y formato con Sabre para no introducir una integración no habilitada.

Ejemplo:

```text
GET /quotes/Q-.../fare-rules
```

Respuesta conceptual:

```json
{
  "quote_id": "Q-...",
  "requires_external_rule_lookup": true,
  "options": [
    {
      "rank": 1,
      "fares": [
        {
          "brand_name": "FLEX",
          "changes": {
            "status": "unknown",
            "source": "not_provided",
            "confidence": "unknown"
          },
          "refunds": {
            "status": "unknown",
            "source": "fare_flag",
            "confidence": "medium"
          }
        }
      ]
    }
  ]
}
```

Política:
- atributo branded explícito → alta confianza;
- `nonRefundable=true` → alta confianza para “no reembolsable”;
- `nonRefundable=false` → no alcanza para afirmar que la devolución está permitida;
- regla ausente → “confirmar reglas tarifarias”.

Pulido UX sobre 0.15:
- Swagger ahora sugiere `{"ranks":[1]}` en vez de rank 0.
- Se mantiene el renderer JSON existente.
- Nuevo `GET /quotes/{quote_id}/whatsapp`: devuelve `text/plain` real, con saltos de línea reales.
- Nuevo `GET /quotes/{quote_id}/email`: devuelve `text/html` real y puede abrirse/renderizarse directamente en navegador.
- No se modifica Sabre, ranking, persistencia ni selección.

Para probar:

```text
POST /quotes/{quote_id}/select
{"ranks":[1]}

GET /quotes/{quote_id}/whatsapp
GET /quotes/{quote_id}/email
```

Los endpoints JSON anteriores siguen disponibles:

```text
GET /quotes/{quote_id}/render?format=whatsapp
GET /quotes/{quote_id}/render?format=email
```

Novedades 0.15 — selección y salida comercial:
- `POST /quotes/{quote_id}/select` guarda los ranks elegidos.
- `DELETE /quotes/{quote_id}/select` limpia la selección.
- `GET /quotes/{quote_id}/render?format=whatsapp` genera texto listo para copiar.
- `GET /quotes/{quote_id}/render?format=email` genera HTML.
- La selección queda persistida en SQLite y el estado pasa a `selected`.
- El renderer usa solamente las opciones seleccionadas.
- Si no hay selección, el render devuelve conflicto en vez de asumir qué enviar.
- La base 0.14 se migra automáticamente agregando `selected_ranks_json`; no hace falta borrar `quotes.db`.
- No se modifica el motor Sabre ni se vuelve a consultar BFM para renderizar.

Flujo recomendado:

```text
POST /agent/quote
      ↓
quote_id
      ↓
GET /quotes/{quote_id}
      ↓
POST /quotes/{quote_id}/select
{"ranks":[1,3]}
      ↓
GET /quotes/{quote_id}/render?format=whatsapp
```

Endpoints:

```text
GET    /health
POST   /quotes/search
POST   /agent/quote
GET    /quotes
GET    /quotes/{quote_id}
POST   /quotes/{quote_id}/select
DELETE /quotes/{quote_id}/select
GET    /quotes/{quote_id}/render?format=whatsapp
GET    /quotes/{quote_id}/render?format=email
```

Ejemplo de selección:

```json
{
  "ranks": [1, 3]
}
```

Novedades 0.14 — persistencia de cotizaciones:
- Cada `/quotes/search` exitoso se guarda por defecto y devuelve `quote_id`.
- `/agent/quote` conserva además el texto original y la interpretación del agente.
- SQLite local en `data/quotes.db` (configurable con `QUOTE_DB_PATH`).
- `GET /quotes` lista las cotizaciones recientes.
- `GET /quotes/{quote_id}` recupera request, response, texto del agente y cotización completa.
- El repositorio está separado del motor Sabre para poder migrar luego a PostgreSQL sin cambiar la lógica de shopping.
- Puede desactivarse la persistencia en `/quotes/search` enviando `"persist": false`.

Ejemplo de ID:

```text
Q-20260814-A1B2C3D4
```

Flujo:

```text
/agent/quote o /quotes/search
        ↓
Sabre BFM + normalización + ranking
        ↓
QuoteSearchAPIResponse
        ↓
SQLite QuoteRepository
        ↓
quote_id
```

Endpoints actuales:

```text
GET  /health
POST /quotes/search
GET  /quotes
GET  /quotes/{quote_id}
POST /agent/quote
```

Para cambiar la ubicación de la base:

```powershell
$env:QUOTE_DB_PATH="C:\dev\sabre_auto\data\quotes.db"
uvicorn app.main:app --reload
```

Novedades 0.13.1 — pasajeros:
- Nuevo modelo `passengers` con ADT / CHILD / INF.
- Cada CHILD conserva su edad real y BFM recibe `Cxx` (ej. C09, C04).
- Múltiples niños con edades diferentes ya no se agrupan bajo una única `child_age`.
- 12 años o más se transforma a ADT.
- Menores de 2 detectados por edad se tratan como INF sin asiento y se informa la suposición.
- Si el usuario menciona niños sin edad, `/agent/quote` devuelve 422 en lugar de asumir 6 años.
- `SeatsRequested` cuenta ADT + CHILD y excluye INF.
- Se mantienen `adults`, `children`, `child_age` e `infants` por compatibilidad con scripts anteriores, pero `passengers` es la fuente preferida.

Ejemplo:

```json
"passengers": [
  {"type": "ADT", "quantity": 2},
  {"type": "CHILD", "age": 9, "quantity": 1},
  {"type": "CHILD", "age": 4, "quantity": 1}
]
```

BFM:

```json
[
  {"Code": "ADT", "Quantity": 2},
  {"Code": "C09", "Quantity": 1},
  {"Code": "C04", "Quantity": 1}
]
```

Novedades 0.13:
- Parser más natural para pedidos reales de agencia.
- Entiende cantidades escritas: "dos personas", "un niño", etc.
- Interpreta edad infantil simple: "un niño de 7 años".
- "Buenos Aires" se resuelve como AEP en rutas domésticas argentinas y EZE en internacionales.
- Más aeropuertos argentinos por nombre: Rosario, Salta, Tucumán, Iguazú y El Calafate.
- Reconoce frases como "directo por Aerolíneas", "con valijas" y "cualquier compañía menos AR".
- Mantiene interpretación auditable y `execute=false`.

Novedades 0.12.1:
- Si el agente interpreta `direct=true`, ahora también devuelve `max_stops=0`.
- Si detecta un vuelo doméstico argentino, la interpretación fuerza `currency=ARS`.
- Si el usuario pidió USD para cabotaje argentino, agrega una advertencia explicando el override.
- Si ya pidió ARS, agrega la regla como assumption.
- Mantiene `/agent/quote`, `/quotes/search` y toda la lógica Sabre de 0.12.

Novedades 0.12:
- `POST /agent/quote` recibe lenguaje natural.
- El parser v1 es determinístico y auditable: no necesita API key de un LLM.
- Devuelve `interpretation.search_request` antes de ejecutar Sabre.
- `execute=false` permite probar únicamente la interpretación sin consumir BFM.
- Entiende origen/destino por códigos o nombres comunes, rangos de fechas, adultos/niños/infantes, directos, carriers incluidos/excluidos, USD/ARS/BOTH, equipaje/branded fares y cantidad de opciones.
- `/quotes/search` sigue siendo el motor estructurado y determinístico.
- Los scripts de prueba anteriores siguen funcionando.

Arranque:

```powershell
uvicorn app.main:app --reload
```

Swagger:
`http://127.0.0.1:8000/docs`

Primero conviene probar sólo interpretación:

```json
POST /agent/quote
{
  "text": "Cotizame EZE-MIA del 19 al 30 de septiembre, 1 adulto, solo directos, AA o LATAM, exclui AR, USD",
  "environment": "cert",
  "execute": false
}
```

El agente devuelve algo conceptualmente equivalente a:

```json
{
  "origin": "EZE",
  "destination": "MIA",
  "departure_date": "2026-09-19",
  "return_date": "2026-09-30",
  "adults": 1,
  "direct": true,
  "carriers": ["AA", "LA"],
  "excluded_carriers": ["AR"],
  "currency": "USD"
}
```

Cuando la interpretación sea correcta, usar `execute=true` para ejecutar BFM y devolver la cotización.

Novedades 0.11:
- API FastAPI real sobre el motor existente.
- `GET /health`.
- `POST /quotes/search`.
- Request estructurado con ruta, pasajeros, directos, carriers incluidos/excluidos, moneda, branded fares y ranking.
- Response con Transaction IDs, opciones normalizadas/rankeadas y `client_quote`.
- Los scripts PowerShell existentes siguen funcionando.

Arranque local:

```powershell
uvicorn app.main:app --reload
```

Swagger:
`http://127.0.0.1:8000/docs`

Ejemplo de request:

```json
{
  "environment": "cert",
  "origin": "EZE",
  "destination": "MIA",
  "departure_date": "2026-09-19",
  "return_date": "2026-09-30",
  "adults": 1,
  "direct": true,
  "carriers": ["AA", "LA"],
  "excluded_carriers": ["AR"],
  "currency": "USD",
  "fare_preference": "branded",
  "max_options": 5
}
```

Novedades 0.10:
- `--direct` es alias de `--max-stops 0`.
- `--exclude-carrier XX` excluye una aerolínea y puede repetirse.
- `--carrier` sigue siendo repetible para restringir a varias aerolíneas.
- Los carriers indicados con `--carrier` se envían como `VendorPref PreferLevel=Only`; `--exclude-carrier` usa `Unacceptable`.
- Se impide incluir y excluir simultáneamente el mismo carrier.
- Se mantiene toda la lógica 0.9.1 de branded fares, equipaje y Business companion.

Ejemplos:
`--carrier AA --carrier AR --carrier LA`
`--direct`
`--direct --exclude-carrier AR`

# Sabre Quote Agent MVP 0.10

Novedades 0.9.1:
- Cambios y devoluciones se muestran dentro de cada branded fare, no como una condición global del itinerario.
- `nonRefundable` se usa como fallback sólo cuando Sabre no devuelve atributos explícitos de refund para esa marca.
- La fecha límite de emisión queda como condición común sólo si es idéntica en todos los productos mostrados; si difiere, se muestra dentro del producto correspondiente.
- La condición final de disponibilidad permanece común a toda la opción.

Novedades 0.9:
- salida comercial compacta: hasta 2 productos Economy, 1 Premium Economy y 1 Business;
- productos intermedios como AA Main Plus se preservan en JSON pero se ocultan si existe una Economy Flexible;
- branded fares se ordenan/seleccionan por cabina y precio para presentación;
- en búsquedas `branded` o `auto`, una búsqueda Business complementaria se ejecuta por defecto;
- Business sólo se une cuando la firma completa de vuelos coincide exactamente;
- raw Business se guarda como `raw_sabre_response_<currency>_business.json`;
- puede desactivarse con `--no-business-companion`.

# Sabre Quote Agent MVP 0.7

Novedades 0.7:
- round trip, open jaw, circle trip y multi-city mediante múltiples OriginDestinationInformation.
- `--leg ORIGEN,DESTINO,FECHA[,HORA]` repetible.
- `--fare-preference baggage` usa `FreePieceRequired=true`.
- `--fare-preference branded`/`auto` solicita Multiple Branded Fares (hasta 3).
- duración y escalas siguen usándose internamente para ranking, pero ya no se muestran al cliente.
- ranking corregido para no contar los días en destino como duración ni el regreso como una escala.

# Sabre Quote Agent MVP 0.4.2

MVP read-only en Python para consultar Sabre Bargain Finder Max REST v5, normalizar itinerarios y generar una cotización de texto.

## Cambios de 0.4

- OAuth v3 `password` compatible con las credenciales PROD verificadas.
- Dos perfiles de request BFM:
  - `official`: replica el ejemplo mínimo del OpenAPI compartido.
  - `standard`: agrega cabina, moneda, equipaje y disponibilidad explícita.
- `RequestType` fijo y válido: `50ITINS`.
- Hora local configurable; default `12:00:00`, evitando usar medianoche como preferencia implícita.
- PTC infantil por edad (`C06`, `C08`, etc.).
- Validación local del envelope obligatorio antes de enviar.
- Archivo `output/bfm_diagnostics.json` con Transaction ID, mensajes e itinerary count.
- Mensaje claro cuando Sabre devuelve `No Availability`.

## Instalación

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip setuptools wheel
python -m pip install -e ".[dev]"
python -m pytest -q
```

## Probar token

```powershell
python scripts/test_token.py
```

## Primera prueba recomendada: perfil oficial

```powershell
python scripts/test_sabre_search.py `
  --origin EZE `
  --destination MIA `
  --departure 2026-09-19 `
  --adults 1 `
  --max-stops 1 `
  --max-options 5 `
  --profile official
```

El perfil `official` omite cabina, moneda y equipaje deliberadamente para reproducir el request mínimo del OpenAPI y aislar cualquier restricción problemática.

## Segunda prueba: perfil estándar

```powershell
python scripts/test_sabre_search.py `
  --origin EZE `
  --destination MIA `
  --departure 2026-09-19 `
  --adults 1 `
  --cabin ECONOMY `
  --max-stops 1 `
  --max-options 5 `
  --profile standard
```

## Archivos generados

- `output/shopping_request.json`
- `output/raw_sabre_response.json`
- `output/bfm_diagnostics.json`
- `output/normalized_itineraries.json`
- `output/client_quote.txt`

## Seguridad

El proyecto permanece en modo read-only para PROD. El endpoint permitido por defecto es `/v5/offers/shop`.

## Support bundle para Sabre

La versión 0.4.2 agrega un bundle de diagnóstico sanitizado para adjuntar a casos de soporte.

Ejemplo PROD:

```powershell
python scripts/test_sabre_search.py `
  --env prod `
  --origin EZE `
  --destination MIA `
  --departure 2026-09-19 `
  --adults 1 `
  --cabin ECONOMY `
  --max-options 5 `
  --support-bundle
```

Ejemplo CERT:

```powershell
python scripts/test_sabre_search.py `
  --env cert `
  --origin EZE `
  --destination MIA `
  --departure 2026-09-19 `
  --adults 1 `
  --cabin ECONOMY `
  --max-options 5 `
  --support-bundle
```

Se genera `logs/<timestamp>_bfm_<ruta>_<debug-id>.zip` con:

- `summary.json`: entorno, PCC, Client ID, OAuth, endpoint, HTTP status, Transaction ID, mensajes Sabre y criterios de búsqueda.
- `request.json`: body JSON exacto enviado a BFM.
- `response.json` o `response.txt`: respuesta del endpoint.
- `response_headers.json`: solo headers de respuesta permitidos para diagnóstico.
- `README.txt`: resumen del paquete.

El generador elimina o bloquea `Authorization`, tokens, Client Secret y password antes de escribir el ZIP. El Client ID y PCC sí se incluyen porque son identificadores útiles para soporte y no son secretos.

## Selección de entorno

- `.env` se usa con `--env prod`.
- `.env.cert` se usa con `--env cert`.

Conviene limpiar variables `SABRE_*` heredadas de PowerShell si se sospecha que pisan los archivos `.env`:

```powershell
Get-ChildItem Env:SABRE_* | Remove-Item
```

## Reglas de moneda (v0.5)

El perfil normal de cotización es `standard`.

- **Doméstico Argentina:** siempre se solicita `CurrencyCode=ARS` (regla MARS), aunque se pida USD o BOTH.
- **Internacional AUTO/USD:** se solicita `CurrencyCode=USD` (regla MUSD).
- **Internacional ARS:** se solicita `CurrencyCode=ARS` (regla MARS). El normalizador extrae el impuesto `Q1` desde `taxDescs` y lo muestra por separado en la cotización.
- **Internacional BOTH:** se hacen dos búsquedas BFM, una USD y una ARS, y se emparejan itinerarios por vuelos/horarios para mostrar ambos importes.

Ejemplos:

```powershell
# Internacional, USD por defecto
python scripts/test_sabre_search.py --env cert --origin EZE --destination MIA --departure 2026-09-19 --currency AUTO

# Internacional en ARS + Q1
python scripts/test_sabre_search.py --env cert --origin EZE --destination MIA --departure 2026-09-19 --currency ARS

# Internacional en USD y ARS
python scripts/test_sabre_search.py --env cert --origin EZE --destination MIA --departure 2026-09-19 --currency BOTH

# Doméstico: fuerza ARS aun si se solicita USD
python scripts/test_sabre_search.py --env cert --origin AEP --destination COR --departure 2026-09-19 --currency USD
```

`--profile official` queda reservado para diagnóstico y no debe usarse para la cotización comercial, porque deliberadamente omite preferencias adicionales como la moneda.

## Ranking y salida comercial (v0.6)

La búsqueda ordena las opciones antes de generar `client_quote.txt`.

```powershell
--sort balanced   # default: precio + escalas + duración
--sort price      # menor tarifa primero
--sort duration   # menor duración total primero
--sort stops      # menos escalas primero
```

El modo `balanced` usa una heurística transparente: ratio de precio frente a la opción más barata + 15% por escala + 3% por cada hora adicional frente a la opción más rápida. No altera tarifas; solo ordena las opciones.

También se genera `output/ranked_itineraries.json` con el ranking y sus métricas.
