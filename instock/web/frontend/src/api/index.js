import axios from 'axios'

const http = axios.create({ timeout: 60000 })

export const fetchTableMeta = (table) =>
  http.get(`/api/meta?name=${table}`)

export const fetchTableData = (table, date) =>
  http.get(`/api/data?name=${table}&date=${date}`)

export const fetchTradeDate = () =>
  http.get('/api/trade_date')

export const fetchIndicators = (code, date, name) =>
  http.get(`/api/indicators?code=${code}&date=${date}&name=${encodeURIComponent(name)}`)

export const toggleAttention = (code, otype) =>
  http.get(`/instock/control/attention?code=${code}&otype=${otype}`)

export const getSyncStatus = () =>
  http.get('/instock/api/sync/status')

export const triggerSync = (key, startDate, endDate) =>
  http.post('/instock/api/sync', { key, start_date: startDate, end_date: endDate })
