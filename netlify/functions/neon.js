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
      return { statusCode: 400, headers: cors, body: JSON.stringify({ error: 'No query' }) };
    }

    // Use neon's tagged template with unsafe for raw SQL
    // This is the correct API for @neondatabase/serverless
    let rows = [];

    if (params && params.length > 0) {
      // Parameterized query
      rows = await sql(query, params);
    } else {
      // Raw query - neon tagged template
      rows = await sql([query]);
    }

    rows = Array.isArray(rows) ? rows : [];
    const command = query.trim().toUpperCase().startsWith('INSERT') ? 'INSERT' :
                    query.trim().toUpperCase().startsWith('CREATE') ? 'CREATE' : 'SELECT';

    return {
      statusCode: 200,
      headers: cors,
      body: JSON.stringify({ rows, command, rowCount: rows.length })
    };
  } catch (e) {
    console.error('Error:', e.message);
    return {
      statusCode: 500,
      headers: cors,
      body: JSON.stringify({ error: e.message })
    };
  }
};
