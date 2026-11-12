-- Funciones operativas de la tienda demo; nunca se aplica sobre Atlas.
ALTER TABLE tienda.productos ADD COLUMN IF NOT EXISTS proveedor text NOT NULL DEFAULT 'Raíz Viva Mayorista (demo)';
CREATE TABLE IF NOT EXISTS tienda.ordenes_compra (
    id bigserial PRIMARY KEY,
    accion_ref uuid NOT NULL,
    proveedor text NOT NULL,
    estado text NOT NULL DEFAULT 'en_camino' CHECK (estado IN ('en_camino', 'recibida', 'cancelada')),
    total bigint NOT NULL CHECK (total >= 0),
    fecha timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE tienda.ordenes_compra DROP CONSTRAINT IF EXISTS ordenes_compra_accion_ref_key;
CREATE UNIQUE INDEX IF NOT EXISTS ordenes_compra_accion_proveedor ON tienda.ordenes_compra(accion_ref, proveedor);
CREATE TABLE IF NOT EXISTS tienda.orden_compra_items (
    id bigserial PRIMARY KEY,
    orden_id bigint NOT NULL REFERENCES tienda.ordenes_compra(id),
    producto_id uuid NOT NULL REFERENCES tienda.productos(id),
    cantidad integer NOT NULL CHECK (cantidad > 0),
    costo_unitario integer NOT NULL CHECK (costo_unitario >= 0),
    total bigint NOT NULL CHECK (total >= 0),
    UNIQUE (orden_id, producto_id)
);
CREATE TABLE IF NOT EXISTS tienda.tareas (
    id bigserial PRIMARY KEY,
    texto text NOT NULL,
    fecha_hora timestamptz NOT NULL,
    estado text NOT NULL DEFAULT 'pendiente' CHECK (estado IN ('pendiente', 'completada')),
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS tareas_pendientes_fecha ON tienda.tareas(estado, fecha_hora);
