const { neon } = require('@neondatabase/serverless');

const DB_URL = 'postgresql://neondb_owner:npg_3inxfuloOrK6@ep-twilight-wildflower-ajjlpuv4-pooler.c-3.us-east-2.aws.neon.tech/neondb?sslmode=require';

exports.handler = async (event) => {
  const cors = {
    'Access-Control-Allow-Origin': '*',
    'Access-Control-Allow-Headers': 'Content-Type',
    'Access-Control-Allow-Methods': 'POST, OPTIONS',
    'Content-Type': 'application/json'
  };

  if (event.httpMethod === 'OPTIONS') {
    return { statusCode: 200, headers: cors, body: '' };
  }

  if (event.httpMethod !== 'POST') {
    return { statusCode: 405, headers: cors, body: JSON.stringify({ error: 'Method not allowed' }) };
  }

  try {
    const sql = neon(DB_URL);
    const { query, params } = JSON.parse(event.body || '{}');

    if (!query) {
      return { statusCode: 400, headers: cors, body: JSON.stringify({ error: 'No query provided' }) };
    }

    // Create table if needed
    await sql`CREATE TABLE IF NOT EXISTS quiz_resultados (
      id SERIAL PRIMARY KEY,
      motorista_nome TEXT,
      motorista_id TEXT,
      telefone TEXT,
      cidade TEXT,
      plataforma TEXT,
      pontuacao INTEGER,
      total_perguntas INTEGER,
      percentagem NUMERIC(5,2),
      data_teste TIMESTAMP DEFAULT NOW()
    )`;

    // Execute query with tagged template (works for all cases)
    let rows = [];
    if (params && params.length > 0) {
      // Build tagged template dynamically using neon's query method
      const result = await sql(query, params);
      rows = Array.isArray(result) ? result : [];
    } else {
      const result = await sql`${neon.unsafe(query)}`;
      rows = Array.isArray(result) ? result : [];
    }

    const command = query.trim().toUpperCase().startsWith('INSERT') ? 'INSERT' : 'SELECT';

    return {
      statusCode: 200,
      headers: cors,
      body: JSON.stringify({ rows, command, rowCount: rows.length })
    };
  } catch (e) {
    console.error('Neon error:', e.message);
    return {
      statusCode: 500,
      headers: cors,
      body: JSON.stringify({ error: e.message })
    };
  }
};
