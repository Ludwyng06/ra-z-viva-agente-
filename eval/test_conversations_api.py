import sys
import unittest
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from starlette.testclient import TestClient
from app.main import app, generate_title
from app.db import agent_db


class TestConversationsAPI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)

    def setUp(self):
        self.created_ids = []

    def tearDown(self):
        with agent_db() as conn:
            for cid in self.created_ids:
                conn.execute('DELETE FROM conversations WHERE id = %s', (cid,))

    def test_generate_title(self):
        text = "  ¿Cuántos pedidos van este mes en la tienda Raíz Viva?  "
        title = generate_title(text)
        words = title.split()
        self.assertLessEqual(len(words), 8)
        self.assertEqual(title, "¿Cuántos pedidos van este mes en la tienda")

        short_text = "Hola agente"
        self.assertEqual(generate_title(short_text), "Hola agente")

        empty_text = "    "
        self.assertEqual(generate_title(empty_text), "Nueva conversación")

    def test_list_conversations(self):
        res = self.client.get('/api/conversations')
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertIsInstance(data, list)
        self.assertLessEqual(len(data), 100)
        if data:
            item = data[0]
            self.assertIn('id', item)
            self.assertIn('title', item)
            self.assertIn('updatedAt', item)
            self.assertIn('messageCount', item)
            self.assertIsInstance(item['messageCount'], int)

    def test_get_conversation_detail_and_messages(self):
        cid = uuid.uuid4()
        self.created_ids.append(cid)
        with agent_db() as conn:
            conn.execute('INSERT INTO conversations(id, title) VALUES (%s, %s)', (cid, 'Test Chat Detalle'))
            conn.execute("INSERT INTO messages(conversation_id, role, content) VALUES (%s, 'user', 'Mensaje de prueba')", (cid,))
            conn.execute("INSERT INTO messages(conversation_id, role, content) VALUES (%s, 'assistant', 'Respuesta de prueba')", (cid,))

        res = self.client.get(f'/api/conversations/{cid}')
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertEqual(body['conversationId'], str(cid))
        self.assertEqual(body['title'], 'Test Chat Detalle')
        self.assertEqual(len(body['messages']), 2)
        self.assertEqual(body['messages'][0]['role'], 'user')
        self.assertEqual(body['messages'][0]['content'], 'Mensaje de prueba')

    def test_rename_conversation(self):
        cid = uuid.uuid4()
        self.created_ids.append(cid)
        with agent_db() as conn:
            conn.execute('INSERT INTO conversations(id, title) VALUES (%s, %s)', (cid, 'Original Title'))

        res = self.client.patch(f'/api/conversations/{cid}', json={'title': 'Nuevo Título Modificado'})
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertEqual(body['title'], 'Nuevo Título Modificado')

        with agent_db() as conn:
            row = conn.execute('SELECT title FROM conversations WHERE id = %s', (cid,)).fetchone()
            self.assertEqual(row['title'], 'Nuevo Título Modificado')

        # Empty title validation
        bad_res = self.client.patch(f'/api/conversations/{cid}', json={'title': '   '})
        self.assertEqual(bad_res.status_code, 422)

    def test_soft_delete_conversation(self):
        cid = uuid.uuid4()
        self.created_ids.append(cid)
        with agent_db() as conn:
            conn.execute('INSERT INTO conversations(id, title) VALUES (%s, %s)', (cid, 'Chat To Delete'))

        res = self.client.delete(f'/api/conversations/{cid}')
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.json().get('ok'))

        # Check soft delete in database
        with agent_db() as conn:
            row = conn.execute('SELECT deleted_at FROM conversations WHERE id = %s', (cid,)).fetchone()
            self.assertIsNotNone(row['deleted_at'])

        # Listing should not include deleted conversation
        list_res = self.client.get('/api/conversations')
        self.assertEqual(list_res.status_code, 200)
        ids = [item['id'] for item in list_res.json()]
        self.assertNotIn(str(cid), ids)

        # GET /api/conversations/{id} should return 404
        get_res = self.client.get(f'/api/conversations/{cid}')
        self.assertEqual(get_res.status_code, 404)

        # Deleting again should return 404
        del_again = self.client.delete(f'/api/conversations/{cid}')
        self.assertEqual(del_again.status_code, 404)

    def test_updated_at_trigger_on_message_insert(self):
        cid = uuid.uuid4()
        self.created_ids.append(cid)
        with agent_db() as conn:
            conn.execute("INSERT INTO conversations(id, title, updated_at) VALUES (%s, %s, now() - interval '1 hour')", (cid, 'Trigger Test'))
            t1 = conn.execute('SELECT updated_at FROM conversations WHERE id = %s', (cid,)).fetchone()['updated_at']
            conn.execute("INSERT INTO messages(conversation_id, role, content) VALUES (%s, 'user', 'Nuevo mensaje para trigger')", (cid,))
            t2 = conn.execute('SELECT updated_at FROM conversations WHERE id = %s', (cid,)).fetchone()['updated_at']
            self.assertGreater(t2, t1)


if __name__ == '__main__':
    unittest.main()
