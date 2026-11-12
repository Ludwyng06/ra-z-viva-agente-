CREATE SCHEMA IF NOT EXISTS tienda;

CREATE TABLE IF NOT EXISTS tienda.productos (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    sku text NOT NULL UNIQUE,
    nombre text NOT NULL,
    categoria text NOT NULL,
    precio_venta integer NOT NULL,
    costo integer NOT NULL,
    stock integer NOT NULL DEFAULT 0,
    stock_minimo integer NOT NULL DEFAULT 5,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS tienda.clientes (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    nombre text NOT NULL,
    ciudad text NOT NULL,
    departamento text NOT NULL,
    telefono text NOT NULL DEFAULT '300 000 0000',
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS tienda.transportadoras (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    nombre text NOT NULL UNIQUE,
    dias_promedio integer NOT NULL DEFAULT 3,
    activo boolean NOT NULL DEFAULT true
);

CREATE TABLE IF NOT EXISTS tienda.tarifas (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    transportadora_id uuid NOT NULL REFERENCES tienda.transportadoras(id) ON DELETE CASCADE,
    departamento text NOT NULL,
    valor_base integer NOT NULL,
    valor_por_kg integer NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS tienda.pedidos (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    numero text NOT NULL UNIQUE,
    fecha timestamptz NOT NULL DEFAULT now(),
    cliente_id uuid NOT NULL REFERENCES tienda.clientes(id),
    ciudad text NOT NULL,
    departamento text NOT NULL,
    transportadora_id uuid REFERENCES tienda.transportadoras(id),
    estado text NOT NULL CHECK(estado IN ('nuevo','confirmado','despachado','entregado','novedad','devolucion','cancelado')),
    metodo_pago text NOT NULL CHECK(metodo_pago IN ('contra_entrega','pagado')),
    subtotal integer NOT NULL,
    flete integer NOT NULL DEFAULT 0,
    total integer NOT NULL,
    creado_por text NOT NULL DEFAULT 'tienda' CHECK(creado_por IN ('tienda','copiloto')),
    motivo_revision text,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS tienda.pedido_items (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    pedido_id uuid NOT NULL REFERENCES tienda.pedidos(id) ON DELETE CASCADE,
    producto_id uuid NOT NULL REFERENCES tienda.productos(id),
    cantidad integer NOT NULL CHECK(cantidad > 0),
    precio_unitario integer NOT NULL,
    total integer NOT NULL
);

CREATE TABLE IF NOT EXISTS tienda.novedades (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    pedido_id uuid NOT NULL REFERENCES tienda.pedidos(id) ON DELETE CASCADE,
    tipo text NOT NULL,
    detalle text NOT NULL,
    estado text NOT NULL CHECK(estado IN ('abierta','resuelta')),
    fecha timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS tienda.acciones (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    accion_ref uuid,
    tipo text NOT NULL,
    pedido_numero text NOT NULL,
    detalle jsonb NOT NULL DEFAULT '{}'::jsonb,
    ejecutado_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS pedidos_fecha_idx ON tienda.pedidos(fecha);
CREATE INDEX IF NOT EXISTS pedidos_estado_idx ON tienda.pedidos(estado);
CREATE INDEX IF NOT EXISTS pedidos_numero_idx ON tienda.pedidos(numero);
CREATE INDEX IF NOT EXISTS novedades_pedido_idx ON tienda.novedades(pedido_id);
CREATE INDEX IF NOT EXISTS novedades_estado_idx ON tienda.novedades(estado);

-- Actualizar pending_actions para permitir acciones de demo (crear, confirmar, cancelar)
ALTER TABLE pending_actions DROP CONSTRAINT IF EXISTS pending_actions_kind_check;
ALTER TABLE pending_actions DROP CONSTRAINT IF EXISTS pending_actions_status_check;
ALTER TABLE pending_actions ADD COLUMN IF NOT EXISTS payload jsonb DEFAULT '{}'::jsonb;
ALTER TABLE pending_actions ALTER COLUMN order_ref DROP NOT NULL;
ALTER TABLE pending_actions ADD CONSTRAINT pending_actions_status_check CHECK(status IN ('pending','confirmed','confirmed_simulated','cancelled'));

-- Borradores de pedido para creación conversacional en varios turnos
CREATE TABLE IF NOT EXISTS tienda.borradores_pedido (
    conversation_id uuid PRIMARY KEY,
    cliente_nombre text,
    ciudad text,
    departamento text,
    direccion text,
    productos jsonb NOT NULL DEFAULT '[]'::jsonb,
    transportadora text,
    metodo_pago text,
    estado text NOT NULL DEFAULT 'en_progreso',
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

