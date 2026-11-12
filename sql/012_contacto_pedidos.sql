-- Contacto aportado para el pedido propio.
ALTER TABLE tienda.borradores_pedido ADD COLUMN IF NOT EXISTS telefono text;
ALTER TABLE tienda.pedidos ADD COLUMN IF NOT EXISTS direccion text;
ALTER TABLE tienda.pedidos ADD COLUMN IF NOT EXISTS telefono text;
