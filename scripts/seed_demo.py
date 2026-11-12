"""Poblar datos realistas de la tienda ficticia Raíz Viva Naturales en el esquema tienda."""
import random
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.config import BOGOTA, ROOT
from app.db import demo_db


PRODUCTOS = [
    ('RV-TEV-01', 'Té Verde Orgánico (20 bolsitas)', 'Infusiones', 24000, 9000, 28, 10),
    ('RV-TEM-02', 'Té de Manzanilla Natural (20 bolsitas)', 'Infusiones', 22000, 8000, 35, 10),
    ('RV-MIE-03', 'Miel de Abejas Pura (500g)', 'Alimentos Saludables', 38000, 18000, 4, 10),  # stock bajo
    ('RV-ACO-04', 'Aceite de Coco Prensado en Frío (300ml)', 'Cuidado Personal', 45000, 20000, 18, 8),
    ('RV-ALA-05', 'Aceite Esencial de Lavanda (15ml)', 'Cuidado Personal', 32000, 14000, 3, 8),   # stock bajo
    ('RV-COL-06', 'Colágeno Hidrolizado en Polvo (300g)', 'Suplementos', 68000, 32000, 0, 15),    # stock 0
    ('RV-CUR-07', 'Cúrcuma en Polvo Pura (200g)', 'Alimentos Saludables', 26000, 11000, 25, 10),
    ('RV-JAB-08', 'Jabón Artesanal de Avena y Miel', 'Cuidado Personal', 18000, 6500, 42, 15),
    ('RV-MOR-09', 'Cápsulas de Moringa (60 unidades)', 'Suplementos', 42000, 19000, 5, 12),       # stock bajo
    ('RV-VIN-10', 'Vinagre de Manzana con Madre (500ml)', 'Alimentos Saludables', 29000, 13000, 30, 10),
    ('RV-GRA-11', 'Granola Artesanal con Frutos Secos (400g)', 'Alimentos Saludables', 25000, 10500, 40, 12),
    ('RV-MAN-12', 'Mantequilla de Maní Natural (350g)', 'Alimentos Saludables', 28000, 12000, 22, 10),
    ('RV-ARO-13', 'Aceite de Romero Puro (30ml)', 'Cuidado Personal', 34000, 15000, 19, 8),
    ('RV-CHI-14', 'Semillas de Chía Orgánica (250g)', 'Alimentos Saludables', 20000, 8500, 31, 10),
]

TRANSPORTADORAS = [
    ('Interrapidísimo', 3),
    ('Envía', 3),
    ('Coordinadora', 2),
    ('TCC', 3),
    ('Servientrega', 3),
    ('Veloces', 2),
    ('99 Minutos', 1),
]

DEPARTAMENTOS_TARIFAS = [
    ('Bogotá D.C.', {'99 Minutos': (9500, 1200), 'Coordinadora': (11000, 1500), 'Interrapidísimo': (10000, 1300),
                     'Envía': (10500, 1400), 'Servientrega': (11500, 1500), 'Veloces': (10000, 1300), 'TCC': (12000, 1600)}),
    ('Cundinamarca', {'99 Minutos': (10500, 1300), 'Coordinadora': (11500, 1500), 'Interrapidísimo': (10500, 1400),
                      'Envía': (11000, 1500), 'Servientrega': (12000, 1600), 'Veloces': (11000, 1400), 'TCC': (12500, 1700)}),
    ('Antioquia', {'99 Minutos': (12000, 1400), 'Coordinadora': (12000, 1500), 'Interrapidísimo': (11500, 1400),
                   'Envía': (11500, 1400), 'Servientrega': (12500, 1600), 'Veloces': (11500, 1400), 'TCC': (13000, 1700)}),
    ('Valle del Cauca', {'Coordinadora': (12500, 1600), 'Interrapidísimo': (12000, 1500), 'Envía': (12000, 1500),
                         'Servientrega': (13000, 1700), 'Veloces': (12500, 1500), 'TCC': (13500, 1800)}),
    ('Atlántico', {'Coordinadora': (13500, 1700), 'Interrapidísimo': (13000, 1600), 'Envía': (13000, 1600),
                   'Servientrega': (14000, 1800), 'Veloces': (13500, 1700), 'TCC': (14500, 1900)}),
    ('Santander', {'Coordinadora': (12500, 1600), 'Interrapidísimo': (12000, 1500), 'Envía': (12000, 1500),
                   'Servientrega': (13500, 1700), 'Veloces': (12500, 1600), 'TCC': (14000, 1800)}),
    ('Risaralda', {'Coordinadora': (12000, 1500), 'Interrapidísimo': (11500, 1400), 'Envía': (11500, 1400),
                   'Servientrega': (12500, 1600), 'Veloces': (12000, 1500), 'TCC': (13000, 1700)}),
    ('Nariño', {'Coordinadora': (15000, 1900), 'Interrapidísimo': (14000, 1800), 'Envía': (14500, 1800),
                'Servientrega': (15500, 2000), 'Veloces': (15000, 1900), 'TCC': (16000, 2100)}),
    ('Nacional', {'Coordinadora': (14500, 1800), 'Interrapidísimo': (13500, 1700), 'Envía': (14000, 1700),
                  'Servientrega': (15000, 1900), 'Veloces': (14500, 1800), 'TCC': (15500, 2000), '99 Minutos': (14000, 1800)}),
]

CIUDADES = [
    ('Bogotá', 'Bogotá D.C.'),
    ('Medellín', 'Antioquia'),
    ('Cali', 'Valle del Cauca'),
    ('Barranquilla', 'Atlántico'),
    ('Bucaramanga', 'Santander'),
    ('Pereira', 'Risaralda'),
    ('Cartagena', 'Bolívar'),
    ('Manizales', 'Caldas'),
    ('Pasto', 'Nariño'),
    ('Ibagué', 'Tolima'),
    ('Santa Marta', 'Magdalena'),
    ('Villavicencio', 'Meta'),
    ('Neiva', 'Huila'),
    ('Armenia', 'Quindío'),
    ('Cúcuta', 'Norte de Santander'),
    ('Tunja', 'Boyacá'),
    ('Montería', 'Córdoba'),
    ('Popayán', 'Cauca'),
    ('Valledupar', 'Cesar'),
    ('Sincelejo', 'Sucre'),
    ('Bello', 'Antioquia'),
    ('Soledad', 'Atlántico'),
    ('Envigado', 'Antioquia'),
    ('Palmira', 'Valle del Cauca'),
    ('Floridablanca', 'Santander'),
    ('Chía', 'Cundinamarca'),
    ('Soacha', 'Cundinamarca'),
    ('Zipaquirá', 'Cundinamarca'),
    ('Dosquebradas', 'Risaralda'),
    ('Girardot', 'Cundinamarca'),
    ('Rionegro', 'Antioquia'),
    ('Buenaventura', 'Valle del Cauca'),
    ('Duitama', 'Boyacá'),
    ('Sogamoso', 'Boyacá'),
    ('Buga', 'Valle del Cauca'),
    ('Tulúa', 'Valle del Cauca'),
    ('Apartadó', 'Antioquia'),
    ('Florencia', 'Caquetá'),
    ('Riohacha', 'La Guajira'),
    ('Barrancabermeja', 'Santander'),
    ('Yopal', 'Casanare'),
    ('Fusagasugá', 'Cundinamarca'),
    ('Facatativá', 'Cundinamarca'),
    ('Ipiales', 'Nariño'),
    ('Cartago', 'Valle del Cauca'),
]

CLIENTES_NOMBRES = [
    'Laura Gómez', 'Carlos Rodríguez', 'Andrés Felipe Torres', 'María Camila Vargas', 'Valentina Restrepo',
    'Juan David Morales', 'Diana Marcela Castro', 'Sebastián Henao', 'Paula Andrea Ríos', 'Santiago Ospina',
    'Natalia Mejía', 'Daniel Betancur', 'Mariana Jaramillo', 'Camilo Andrés Londoño', 'Felipe Cardona',
    'Alejandra Pineda', 'David Esteban Herrera', 'Carolina Duque', 'Mateo Ramírez', 'Gabriela Montoya',
    'Julián David Salazar', 'Daniela Arango', 'Nicolás Echeverry', 'Sara Sofía Muñoz', 'Esteban Zapata',
    'Manuela Castaño', 'Alejandro Giraldo', 'Catalina Cano', 'Diego Fernando Marín', 'Verónica Osorio',
    'Samuel David Rojas', 'Valeria Guzmán', 'Simón Quintero', 'Vanessa Ceballos', 'Lucas Agudelo',
    'Camila Villamizar', 'Manuel José Suárez', 'Andrea Paola Silva', 'Cristian Camilo Vélez', 'Melisa Barrientos',
    'Jorge Iván Bedoya', 'Angie Tatiana Parra', 'Hernán Darío Correa', 'Viviana Marcela Peña', 'Oscar Eduardo Franco',
]


def seed():
    rnd = random.Random(42)
    with demo_db() as conn:
        # Aplicar el esquema primero
        sql_file = ROOT / 'sql/010_tienda_demo.sql'
        if sql_file.exists():
            conn.execute(sql_file.read_text(encoding='utf-8'))

        # Limpiar datos anteriores de la tienda
        conn.execute('''
            TRUNCATE TABLE tienda.acciones, tienda.novedades, tienda.pedido_items,
                           tienda.pedidos, tienda.tarifas, tienda.transportadoras,
                           tienda.clientes, tienda.productos CASCADE
        ''')

        # 1. Insertar productos
        producto_map = {}
        for sku, nombre, cat, p_venta, costo, stock, s_min in PRODUCTOS:
            row = conn.execute('''
                INSERT INTO tienda.productos(sku, nombre, categoria, precio_venta, costo, stock, stock_minimo)
                VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id, sku, nombre, precio_venta, stock
            ''', (sku, nombre, cat, p_venta, costo, stock, s_min)).fetchone()
            producto_map[sku] = row

        # 2. Insertar transportadoras
        transp_map = {}
        for nombre, dias in TRANSPORTADORAS:
            row = conn.execute('''
                INSERT INTO tienda.transportadoras(nombre, dias_promedio, activo)
                VALUES (%s, %s, true) RETURNING id, nombre, dias_promedio
            ''', (nombre, dias)).fetchone()
            transp_map[nombre] = row

        # 3. Insertar tarifas
        for dpto, rates in DEPARTAMENTOS_TARIFAS:
            for t_nombre, (v_base, v_kg) in rates.items():
                if t_nombre in transp_map:
                    conn.execute('''
                        INSERT INTO tienda.tarifas(transportadora_id, departamento, valor_base, valor_por_kg)
                        VALUES (%s, %s, %s, %s)
                    ''', (transp_map[t_nombre]['id'], dpto, v_base, v_kg))

        # 4. Insertar clientes
        cliente_rows = []
        for i, nombre in enumerate(CLIENTES_NOMBRES):
            ciudad, dpto = CIUDADES[i % len(CIUDADES)]
            tel = f'300 000 {i+1000:04d}'  # Enmascarado ficticio
            row = conn.execute('''
                INSERT INTO tienda.clientes(nombre, ciudad, departamento, telefono)
                VALUES (%s, %s, %s, %s) RETURNING id, nombre, ciudad, departamento
            ''', (nombre, ciudad, dpto, tel)).fetchone()
            cliente_rows.append(row)

        # 5. Generar pedidos
        # Fechas: mes actual es noviembre 2026. Hoy es 2026-11-04.
        hoy = datetime.now(BOGOTA).replace(hour=14, minute=30, second=0, microsecond=0)
        ayer = hoy - timedelta(days=1)
        hace_2_dias = hoy - timedelta(days=2)
        hace_3_dias = hoy - timedelta(days=3)

        # Lista de transportadoras disponibles para selección
        transp_nombres = list(transp_map.keys())

        # Productos que tienen stock disponible (> 0)
        prod_disponibles = [p for p in PRODUCTOS if p[0] != 'RV-COL-06']

        # 80 pedidos del mes actual:
        # Mezcla realista:
        # ~50% confirmados/despachados/entregados (12 entregados, 16 despachados, 12 confirmados)
        # ~20% nuevos (16 nuevos)
        # ~10% cancelados (8 cancelados)
        # ~10% novedad (8 con novedad)
        # ~5% devolucion (4 devoluciones)
        # Total = 80
        # ~25% pagados (20 pagados), 60 contra_entrega
        estados_mes_actual = (
            ['entregado'] * 14 +
            ['despachado'] * 16 +
            ['confirmado'] * 14 +
            ['nuevo'] * 16 +
            ['cancelado'] * 8 +
            ['novedad'] * 8 +
            ['devolucion'] * 4
        )
        rnd.shuffle(estados_mes_actual)

        # 20 pagados, 60 contra entrega
        pagos_mes_actual = ['pagado'] * 20 + ['contra_entrega'] * 60
        rnd.shuffle(pagos_mes_actual)

        # Distribuir fechas del mes actual (Noviembre 2026: 80 pedidos):
        # Nov 4 (hoy): 20 pedidos
        # Nov 3 (ayer): 22 pedidos
        # Nov 2: 20 pedidos
        # Nov 1: 18 pedidos
        fechas_mes_actual = (
            [datetime(2026, 11, 4, rnd.randint(8, 17), rnd.randint(0, 59), tzinfo=BOGOTA) for _ in range(20)] +
            [datetime(2026, 11, 3, rnd.randint(8, 20), rnd.randint(0, 59), tzinfo=BOGOTA) for _ in range(22)] +
            [datetime(2026, 11, 2, rnd.randint(8, 20), rnd.randint(0, 59), tzinfo=BOGOTA) for _ in range(20)] +
            [datetime(2026, 11, 1, rnd.randint(8, 20), rnd.randint(0, 59), tzinfo=BOGOTA) for _ in range(18)]
        )
        rnd.shuffle(fechas_mes_actual)

        motivos_revision_ejemplos = [
            'Dirección incompleta (falta número de apartamento)',
            'Ciudad sin cobertura directa para pago contra entrega',
            'Cliente no confirmó el pedido tras llamadas de verificación',
            'Posible inconsistencia en nomenclatura del destinatario',
            'Zona especial requiere validación de transportadora',
        ]

        nuevos_con_revision = 0
        pedidos_con_novedad = []

        # Crear 60 pedidos del mes anterior (Octubre 2026: RV-1001 a RV-1060)
        for i in range(1, 61):
            numero = f'RV-{1000 + i}'
            cliente = cliente_rows[(i - 1) % len(cliente_rows)]
            t_nombre = rnd.choice(transp_nombres)
            transp = transp_map[t_nombre]
            estado = rnd.choice(['entregado', 'entregado', 'entregado', 'despachado', 'devolucion', 'cancelado'])
            metodo = 'pagado' if (i % 4 == 0) else 'contra_entrega'
            # Fechas en octubre 2026 (días 1 a 31)
            dia_oct = ((i * 3) % 31) + 1
            fecha = datetime(2026, 10, dia_oct, rnd.randint(8, 20), rnd.randint(0, 59), tzinfo=BOGOTA)

            # Items (1 a 3)
            num_items = rnd.choices([1, 2, 3], weights=[0.5, 0.35, 0.15])[0]
            items_elegidos = rnd.sample(prod_disponibles, num_items)
            subtotal = 0
            items_data = []
            for p_tuple in items_elegidos:
                sku = p_tuple[0]
                p_db = producto_map[sku]
                qty = rnd.randint(1, 2)
                p_unit = p_db['precio_venta']
                tot = qty * p_unit
                subtotal += tot
                items_data.append((p_db['id'], qty, p_unit, tot))

            flete = rnd.choice([10500, 11500, 12000, 13500, 14000, 15000])
            total = subtotal + flete

            p_row = conn.execute('''
                INSERT INTO tienda.pedidos(numero, fecha, cliente_id, ciudad, departamento,
                                          transportadora_id, estado, metodo_pago, subtotal,
                                          flete, total, creado_por, motivo_revision)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'tienda', NULL)
                RETURNING id
            ''', (numero, fecha, cliente['id'], cliente['ciudad'], cliente['departamento'],
                  transp['id'], estado, metodo, subtotal, flete, total)).fetchone()

            for prod_id, qty, p_unit, tot in items_data:
                conn.execute('''
                    INSERT INTO tienda.pedido_items(pedido_id, producto_id, cantidad, precio_unitario, total)
                    VALUES (%s, %s, %s, %s, %s)
                ''', (p_row['id'], prod_id, qty, p_unit, tot))

        # Crear 80 pedidos del mes actual (Noviembre 2026: RV-1061 a RV-1140)
        for i in range(1, 81):
            numero = f'RV-{1060 + i}'
            cliente = cliente_rows[(i - 1) % len(cliente_rows)]
            t_nombre = rnd.choice(transp_nombres)
            transp = transp_map[t_nombre]
            estado = estados_mes_actual[i - 1]
            metodo = pagos_mes_actual[i - 1]
            fecha = fechas_mes_actual[i - 1]

            # Garantizar que RV-1065 sea un pedido nuevo con revisión por dirección incompleta
            if numero == 'RV-1065':
                estado = 'nuevo'
                motivo = 'Dirección incompleta (falta número de apartamento)'
            else:
                motivo = None
                if estado == 'nuevo' and nuevos_con_revision < 5:
                    motivo = motivos_revision_ejemplos[nuevos_con_revision % len(motivos_revision_ejemplos)]
                    nuevos_con_revision += 1

            num_items = rnd.choices([1, 2, 3], weights=[0.55, 0.35, 0.10])[0]
            items_elegidos = rnd.sample(prod_disponibles, num_items)
            subtotal = 0
            items_data = []
            for p_tuple in items_elegidos:
                sku = p_tuple[0]
                p_db = producto_map[sku]
                qty = rnd.randint(1, 2)
                p_unit = p_db['precio_venta']
                tot = qty * p_unit
                subtotal += tot
                items_data.append((p_db['id'], qty, p_unit, tot))

            flete = rnd.choice([10000, 11500, 12000, 12500, 13500, 14000, 15000])
            total = subtotal + flete

            p_row = conn.execute('''
                INSERT INTO tienda.pedidos(numero, fecha, cliente_id, ciudad, departamento,
                                          transportadora_id, estado, metodo_pago, subtotal,
                                          flete, total, creado_por, motivo_revision)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'tienda', %s)
                RETURNING id, numero, estado, fecha
            ''', (numero, fecha, cliente['id'], cliente['ciudad'], cliente['departamento'],
                  transp['id'], estado, metodo, subtotal, flete, total, motivo)).fetchone()

            if estado == 'novedad':
                pedidos_con_novedad.append(p_row)

            for prod_id, qty, p_unit, tot in items_data:
                conn.execute('''
                    INSERT INTO tienda.pedido_items(pedido_id, producto_id, cantidad, precio_unitario, total)
                    VALUES (%s, %s, %s, %s, %s)
                ''', (p_row['id'], prod_id, qty, p_unit, tot))

        # 6. Insertar exactamente 6 novedades abiertas realistas
        # Detalles: cliente ausente, dirección errada, rechazado, teléfono apagado, etc.
        novedades_abiertas_data = [
            ('cliente_ausente', 'Cliente no se encontraba en el domicilio en el primer intento de entrega'),
            ('direccion_errada', 'Dirección errada: la nomenclatura indicada no coincide con el barrio'),
            ('rechazado', 'Cliente rechazó el paquete por falta de dinero en efectivo en el momento de la entrega'),
            ('telefono_apagado', 'Teléfono de contacto apagado o no responde para coordinar el ingreso'),
            ('direccion_incompleta', 'Dirección incompleta: el repartidor requiere el número de interior y torre'),
            ('cliente_ausente', 'Segundo intento de entrega fallido por destinatario ausente'),
        ]

        for idx, (tipo, detalle) in enumerate(novedades_abiertas_data):
            p = pedidos_con_novedad[idx % len(pedidos_con_novedad)]
            conn.execute('''
                INSERT INTO tienda.novedades(pedido_id, tipo, detalle, estado, fecha)
                VALUES (%s, %s, %s, 'abierta', %s)
            ''', (p['id'], tipo, detalle, p['fecha']))

        # Insertar 4 novedades resueltas para dar realismo al histórico
        for idx in range(6, len(pedidos_con_novedad)):
            p = pedidos_con_novedad[idx]
            conn.execute('''
                INSERT INTO tienda.novedades(pedido_id, tipo, detalle, estado, fecha)
                VALUES (%s, 'acuerdo_entrega', 'Cliente acordó recepción con conserjería', 'resuelta', %s)
            ''', (p['id'], p['fecha']))

    print('Seed de Raíz Viva Naturales completado exitosamente.')
    print(f'14 productos naturales cargados (3 con stock bajo, 1 en 0).')
    print(f'45 clientes ficticios en ciudades reales de Colombia.')
    print(f'80 pedidos de noviembre 2026 (mes actual) y 60 de octubre 2026 (mes anterior).')
    print(f'6 novedades abiertas realistas registradas.')


if __name__ == '__main__':
    seed()
