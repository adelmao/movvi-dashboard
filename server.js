const express = require('express');
const { neon } = require('@neondatabase/serverless');
const path = require('path');

const app = express();
const PORT = process.env.PORT || 3000;
const DB_URL = 'postgresql://neondb_owner:npg_3inxfuloOrK6@ep-twilight-wildflower-ajjlpuv4-pooler.c-3.us-east-2.aws.neon.tech/neondb?sslmode=require';

app.use(express.json());
app.use(express.static(path.join(__dirname, 'public')));

// API endpoint for Neon queries
app.post('/api/query', async (req, res) => {
  res.setHeader('Access-Control-Allow-Origin', '*');
  try {
    const sql = neon(DB_URL);
    const { query, params } = req.body;
    if (!query) return res.status(400).json({ error: 'No query' });

    await sql`CREATE TABLE IF NOT EXISTS quiz_resultados (
      id SERIAL PRIMARY KEY,
      motorista_nome TEXT, motorista_id TEXT, telefone TEXT,
      cidade TEXT, plataforma TEXT,
      pontuacao INTEGER, total_perguntas INTEGER,
      percentagem NUMERIC(5,2),
      data_teste TIMESTAMP DEFAULT NOW()
    )`;

    let rows;
    if (params && params.length > 0) {
      rows = await sql(query, params);
    } else {
      rows = await sql([query]);
    }
    rows = Array.isArray(rows) ? rows : [];
    const command = query.trim().toUpperCase().startsWith('INSERT') ? 'INSERT' : 'SELECT';
    res.json({ rows, command, rowCount: rows.length });
  } catch (e) {
    console.error(e.message);
    res.status(500).json({ error: e.message });
  }
});

app.listen(PORT, () => console.log(`Movvi Dashboard running on port ${PORT}`));
