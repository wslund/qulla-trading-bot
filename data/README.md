# data/

Lokal datalagring. Allt här utom denna fil och `.gitkeep`-filerna är git-ignorerat.

- `raw/` – data exakt som hämtad från källan, oförändrad.
- `processed/` – rensad och justerad data i Parquet.
- `labels/` – etiketter och utfall kopplade till kandidater via `candidate_id`.

Datakällor, tidsstämplar och licenser dokumenteras när datalagret byggs (fas 4). Lägg aldrig datafiler i git.
