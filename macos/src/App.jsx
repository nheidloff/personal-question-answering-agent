import { useEffect, useMemo, useState } from 'react'
import './styles.css'

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://127.0.0.1:8800'

function formatProgress(progress) {
  if (typeof progress !== 'number') return '0%'
  return `${Math.round(progress)}%`
}

function formatUtcTimestamp(timestamp) {
  if (!timestamp) return 'Not synchronized yet'
  const parsed = new Date(timestamp)
  if (Number.isNaN(parsed.getTime())) return timestamp
  return parsed.toISOString().replace('.000Z', 'Z')
}

function formatUtcShort(timestamp) {
  if (!timestamp) return 'Not synchronized yet'
  const parsed = new Date(timestamp)
  if (Number.isNaN(parsed.getTime())) return timestamp
  return parsed.toISOString().slice(0, 16).replace('T', ' ')
}

function getCurrentPage() {
  return window.location.hash === '#/files' ? 'files' : 'home'
}

function toRelativeDataPath(path) {
  const prefix = 'data/'
  if (!path) return ''
  return path.startsWith(prefix) ? path.slice(prefix.length) : path
}

function comparePathNames(first, second) {
  return first.localeCompare(second, undefined, {
    sensitivity: 'base',
    numeric: true,
  })
}

function sortFilesMetadataByDirectory(filesMetadata) {
  const root = { files: [], subdirectories: new Map() }

  for (const file of filesMetadata) {
    const relativePath = toRelativeDataPath(file.path)
    const segments = relativePath.split('/').filter(Boolean)

    if (segments.length === 0) {
      root.files.push({ file, fileName: relativePath })
      continue
    }

    const fileName = segments[segments.length - 1]
    const directorySegments = segments.slice(0, -1)
    let node = root

    for (const directoryName of directorySegments) {
      if (!node.subdirectories.has(directoryName)) {
        node.subdirectories.set(directoryName, { files: [], subdirectories: new Map() })
      }
      node = node.subdirectories.get(directoryName)
    }

    node.files.push({ file, fileName })
  }

  const orderedFiles = []

  const appendNode = (node) => {
    const sortedFiles = [...node.files].sort((first, second) => comparePathNames(first.fileName, second.fileName))
    for (const entry of sortedFiles) {
      orderedFiles.push(entry.file)
    }

    const sortedSubdirectories = [...node.subdirectories.keys()].sort(comparePathNames)
    for (const subdirectoryName of sortedSubdirectories) {
      appendNode(node.subdirectories.get(subdirectoryName))
    }
  }

  appendNode(root)
  return orderedFiles
}

function uniqueSourceFiles(sources) {
  if (!Array.isArray(sources)) return []

  const seen = new Set()
  const files = []

  for (const source of sources) {
    const path = source?.path || source?.filename
    if (!path || seen.has(path)) continue
    seen.add(path)
    files.push(path)
  }

  return files
}

function App() {
  const [currentPage, setCurrentPage] = useState(getCurrentPage)
  const [jobId, setJobId] = useState('')
  const [jobStatus, setJobStatus] = useState(null)
  const [isStartingIndex, setIsStartingIndex] = useState(false)
  const [isRecreatingIndex, setIsRecreatingIndex] = useState(false)

  const [question, setQuestion] = useState('')
  const [isChatting, setIsChatting] = useState(false)
  const [history, setHistory] = useState([])
  const [currentPair, setCurrentPair] = useState(null)
  const [indexOverview, setIndexOverview] = useState(null)
  const [isLoadingOverview, setIsLoadingOverview] = useState(false)
  const [indexOverviewError, setIndexOverviewError] = useState('')
  const [filesMetadata, setFilesMetadata] = useState([])
  const [filesLastSynchronizedUtc, setFilesLastSynchronizedUtc] = useState(null)
  const [isLoadingFilesMetadata, setIsLoadingFilesMetadata] = useState(false)
  const [filesMetadataError, setFilesMetadataError] = useState('')
  const [dataDir, setDataDir] = useState('')

  const isIndexing = useMemo(() => {
    return jobStatus && (jobStatus.status === 'queued' || jobStatus.status === 'running')
  }, [jobStatus])

  const mergedFilesMetadata = useMemo(() => {
    if (!jobStatus || !jobStatus.indexed_paths || jobStatus.indexed_paths.length === 0) {
      return filesMetadata
    }

    const indexedSet = new Set(jobStatus.indexed_paths)
    return filesMetadata.map((file) => {
      if (indexedSet.has(file.path) && file.status !== 'indexed') {
        return { ...file, status: 'indexed' }
      }
      return file
    })
  }, [filesMetadata, jobStatus])

  const sortedFilesMetadata = useMemo(() => {
    return sortFilesMetadataByDirectory(mergedFilesMetadata)
  }, [mergedFilesMetadata])

  const loadIndexOverview = async () => {
    try {
      setIsLoadingOverview(true)
      setIndexOverviewError('')
      const response = await fetch(`${API_BASE_URL}/api/index/overview`)
      if (!response.ok) {
        throw new Error(`Failed to load index overview (${response.status})`)
      }
      const payload = await response.json()
      setIndexOverview(payload)
      if (payload.data_dir) {
        setDataDir(payload.data_dir)
      }
    } catch (error) {
      setIndexOverviewError(error.message)
    } finally {
      setIsLoadingOverview(false)
    }
  }

  const loadFilesMetadata = async () => {
    try {
      setIsLoadingFilesMetadata(true)
      setFilesMetadataError('')
      const response = await fetch(`${API_BASE_URL}/api/index/files`)
      if (!response.ok) {
        throw new Error(`Failed to load indexed files (${response.status})`)
      }
      const payload = await response.json()
      setFilesMetadata(payload.files || [])
      setFilesLastSynchronizedUtc(payload.last_synchronized_utc || null)
    } catch (error) {
      setFilesMetadataError(error.message)
    } finally {
      setIsLoadingFilesMetadata(false)
    }
  }

  useEffect(() => {
    loadIndexOverview()
  }, [])

  useEffect(() => {
    const onHashChange = () => {
      setCurrentPage(getCurrentPage())
    }
    window.addEventListener('hashchange', onHashChange)
    return () => {
      window.removeEventListener('hashchange', onHashChange)
    }
  }, [])

  useEffect(() => {
    if (currentPage !== 'files') return
    if (filesMetadata.length > 0 || isLoadingFilesMetadata) return
    loadFilesMetadata()
  }, [currentPage])

  useEffect(() => {
    if (!jobId) return
    if (jobStatus && (jobStatus.status === 'completed' || jobStatus.status === 'failed')) return

    let isCancelled = false

    const poll = async () => {
      try {
        const response = await fetch(`${API_BASE_URL}/api/index/status/${jobId}`)
        if (!response.ok) {
          throw new Error(`Status polling failed (${response.status})`)
        }
        const payload = await response.json()
        if (!isCancelled) {
          setJobStatus(payload)
        }
      } catch (error) {
        if (!isCancelled) {
          setJobStatus((prev) => ({
            ...(prev || {}),
            status: 'failed',
            error: error.message,
            message: 'Failed to poll indexing status',
            progress: prev?.progress || 0,
          }))
        }
      }
    }

    poll()
    const timer = setInterval(poll, 1500)

    return () => {
      isCancelled = true
      clearInterval(timer)
    }
  }, [jobId, jobStatus?.status])

  useEffect(() => {
    if (!jobStatus) return
    if (jobStatus.status === 'completed' || jobStatus.status === 'failed') {
      loadIndexOverview()
      if (currentPage === 'files') {
        loadFilesMetadata()
      }
    }
  }, [jobStatus?.status, currentPage])

  const startIndexing = async () => {
    try {
      setIsStartingIndex(true)
      const response = await fetch(`${API_BASE_URL}/api/index/start`, {
        method: 'POST',
      })

      if (!response.ok) {
        throw new Error(`Failed to start indexing (${response.status})`)
      }

      const payload = await response.json()
      setJobId(payload.job_id)
      setJobStatus({
        status: 'queued',
        progress: 0,
        message: 'Queued',
      })
    } catch (error) {
      setJobStatus({
        status: 'failed',
        progress: 0,
        message: 'Could not start indexing',
        error: error.message,
      })
    } finally {
      setIsStartingIndex(false)
    }
  }

  const sendQuestion = async (event) => {
    event.preventDefault()
    const trimmed = question.trim()
    if (!trimmed || isChatting) return

    setIsChatting(true)
    const newUserMsg = { role: 'user', content: trimmed }

    // We temporarily show the user's question while thinking
    setCurrentPair({ question: trimmed, answer: null, sources: [] })
    setQuestion('')

    try {
      const response = await fetch(`${API_BASE_URL}/api/chat`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ question: trimmed }),
      })

      if (!response.ok) {
        const details = await response.text()
        throw new Error(`Chat request failed (${response.status}): ${details}`)
      }

      const payload = await response.json()
      const newPair = {
        question: trimmed,
        answer: payload.answer,
        sources: payload.sources || [],
      }
      setCurrentPair(newPair)
      setHistory((prev) => [newPair, ...prev])
    } catch (error) {
      const errorPair = {
        question: trimmed,
        answer: `Error: ${error.message}`,
        sources: [],
      }
      setCurrentPair(errorPair)
    } finally {
      setIsChatting(false)
    }
  }

  const startNextQuestion = () => {
    setCurrentPair(null)
    setQuestion('')
  }

  const selectHistoryItem = (pair) => {
    setCurrentPair(pair)
  }

  const recreateIndex = async () => {
    if (isStartingIndex || isIndexing || isRecreatingIndex) return
    const confirmed = window.confirm(
      'This will reset index-files.md and fully recreate the OpenSearch index. Continue?'
    )
    if (!confirmed) return

    try {
      setIsRecreatingIndex(true)
      setJobId('')
      setJobStatus({
        status: 'running',
        progress: 0,
        message: 'Re-creating index...',
      })

      const response = await fetch(`${API_BASE_URL}/api/index/recreate`, {
        method: 'POST',
      })

      if (!response.ok) {
        const details = await response.text()
        throw new Error(`Failed to delete index (${response.status}): ${details}`)
      }

      const payload = await response.json()
      setJobStatus({
        status: payload.status || 'completed',
        progress: 100,
        message: payload.message || 'Index deleted',
        result: {
          documents_removed: payload.documents_removed,
          index_deleted: payload.index_deleted,
          index_created: payload.index_created,
          vector_dimension: payload.vector_dimension,
          opensearch_index: payload.opensearch_index,
        },
      })
    } catch (error) {
      setJobStatus({
        status: 'failed',
        progress: 0,
        message: 'Could not delete index',
        error: error.message,
      })
    } finally {
      setIsRecreatingIndex(false)
      loadIndexOverview()
      if (currentPage === 'files') {
        loadFilesMetadata()
      }
    }
  }

  const navigateToPage = (page) => {
    window.location.hash = page === 'files' ? '#/files' : '#/'
  }

  return (
    <div className="app-shell">
      <div className="ambient-shape ambient-a" />
      <div className="ambient-shape ambient-b" />

      <header className="app-header">
        <h1>Personal Question Answering Assistant</h1>
        <p>Chat with your personal files locally. No cloud. No remote model.</p>
      </header>

      <nav className="page-nav">
        <button
          type="button"
          className={`nav-button ${currentPage === 'home' ? 'active' : ''}`}
          onClick={() => navigateToPage('home')}
        >
          Assistant
        </button>
        <button
          type="button"
          className={`nav-button ${currentPage === 'files' ? 'active' : ''}`}
          onClick={() => navigateToPage('files')}
        >
          Files Metadata
        </button>
      </nav>

      {currentPage === 'home' ? (
        <main className="grid-layout assistant-layout">
          <section className="panel chat-panel">
            <h2>Assistant</h2>

            <div className="current-interaction">
              {!currentPair ? (
                <div className="input-mode">
                  <div className="empty-state">Ask a question about your indexed personal data.</div>
                  <form onSubmit={sendQuestion} className="chat-form">
                    <textarea
                      value={question}
                      onChange={(event) => setQuestion(event.target.value)}
                      placeholder="What is in my documents?"
                      rows={3}
                      disabled={isChatting}
                    />
                    <button type="submit" disabled={isChatting || !question.trim()}>
                      {isChatting ? 'Thinking...' : 'Ask Question'}
                    </button>
                  </form>
                </div>
              ) : (
                <div className="display-mode">
                  <article className="msg msg-user">
                    <div className="msg-role">You</div>
                    <div className="msg-content">{currentPair.question}</div>
                  </article>

                  {isChatting ? (
                    <div className="assistant-thinking">
                      <div className="typing-indicator">Assistant is thinking...</div>
                    </div>
                  ) : currentPair.answer ? (
                    <article className="msg msg-assistant">
                      <div className="msg-role">Assistant</div>
                      <div className="msg-content">{currentPair.answer}</div>
                      {uniqueSourceFiles(currentPair.sources).length > 0 && (
                        <div className="source-list">
                          {uniqueSourceFiles(currentPair.sources).map((path) => {
                            const relativeFilePath = toRelativeDataPath(path)
                            const dataUrl = `${API_BASE_URL}/data/${relativeFilePath}`
                            return (
                              <div key={path} className="source-item">
                                <a
                                  href={dataUrl}
                                  className="source-path source-link"
                                  title={path}
                                  target="_blank"
                                  rel="noopener noreferrer"
                                >
                                  {path}
                                </a>
                              </div>
                            )
                          })}
                        </div>
                      )}
                    </article>
                  ) : null}

                  {!isChatting && (
                    <div className="next-action">
                      <button type="button" onClick={startNextQuestion} className="next-button">
                        Ask next Question
                      </button>
                    </div>
                  )}
                </div>
              )}
            </div>
          </section>

          <aside className="panel history-panel">
            <h2>Previous Questions</h2>
            {history.length === 0 ? (
              <div className="empty-history">No questions yet.</div>
            ) : (
              <ul className="history-list">
                {history.map((pair, idx) => (
                  <li key={idx}>
                    <button
                      type="button"
                      className={`history-item ${currentPair === pair ? 'active' : ''}`}
                      onClick={() => selectHistoryItem(pair)}
                    >
                      <span className="history-q">{pair.question}</span>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </aside>
        </main>
      ) : (
        <main className="single-layout">
          <section className="panel control-panel">
            <h2>Data Indexing</h2>
            <p>Index all files from the workspace data directory.</p>

            <div className="control-actions">
              <button onClick={startIndexing} disabled={isStartingIndex || isIndexing || isRecreatingIndex}>
                {isStartingIndex ? 'Starting...' : isIndexing ? 'Indexing in progress...' : 'Start indexing'}
              </button>
              <button
                className="danger-button"
                onClick={recreateIndex}
                disabled={isStartingIndex || isIndexing || isRecreatingIndex}
              >
                {isRecreatingIndex ? 'Re-creating...' : 'Delete Index'}
              </button>
            </div>

            <div className="status-card">
              <div className="status-row">
                <span>Status</span>
                <strong>{jobStatus?.status || 'idle'}</strong>
              </div>
              <div className="status-row">
                <span>Progress</span>
                <strong>{formatProgress(jobStatus?.progress)}</strong>
              </div>
              <div className="status-row message-row">
                <span>Message</span>
                <strong>{jobStatus?.message || 'Waiting for action'}</strong>
              </div>
              {jobId && (
                <div className="status-row">
                  <span>Job ID</span>
                  <strong className="mono">{jobId}</strong>
                </div>
              )}
              {jobStatus?.result && (
                <div className="status-result">
                  {typeof jobStatus.result.files_processed === 'number' && (
                    <div>Files processed: {jobStatus.result.files_processed}</div>
                  )}
                  {typeof jobStatus.result.chunks_indexed === 'number' && (
                    <div>Chunks indexed: {jobStatus.result.chunks_indexed}</div>
                  )}
                  {typeof jobStatus.result.documents_removed === 'number' && (
                    <div>Documents removed: {jobStatus.result.documents_removed}</div>
                  )}
                  {typeof jobStatus.result.index_deleted === 'boolean' && (
                    <div>Index deleted: {jobStatus.result.index_deleted ? 'yes' : 'no (did not exist)'}</div>
                  )}
                  {typeof jobStatus.result.index_created === 'boolean' && (
                    <div>Index created: {jobStatus.result.index_created ? 'yes' : 'no'}</div>
                  )}
                  {jobStatus.result.opensearch_index && <div>OpenSearch index: {jobStatus.result.opensearch_index}</div>}
                </div>
              )}
              {jobStatus?.error && <div className="status-error">{jobStatus.error}</div>}
            </div>

            <div className="status-card">
              <div className="status-row">
                <span>Files in data directory</span>
                <strong>{isLoadingOverview && !indexOverview ? 'Loading...' : (indexOverview?.data_files_total ?? '-')}</strong>
              </div>
              <div className="status-row">
                <span>Chunks in <a href="http://localhost:5601/app/opensearch_index_management_dashboards#/indices">OpenSearch</a></span>
                <strong>{isLoadingOverview && !indexOverview ? 'Loading...' : (indexOverview?.opensearch_chunks_count ?? '-')}</strong>
              </div>
              <div className="status-row">
                <span>Indexed files</span>
                <strong>{isLoadingOverview && !indexOverview ? 'Loading...' : (indexOverview?.indexed_files ?? '-')}</strong>
              </div>
              <div className="status-row">
                <span>Files failed to index</span>
                <strong>{isLoadingOverview && !indexOverview ? 'Loading...' : (indexOverview?.failed_files ?? '-')}</strong>
              </div>
              <div className="status-row message-row">
                <span>Last synchronized</span>
                <strong>{isLoadingOverview && !indexOverview ? 'Loading...' : formatUtcShort(indexOverview?.last_synchronized_utc)}</strong>
              </div>
              {indexOverview?.opensearch_status === 'unavailable' && indexOverview?.opensearch_error && (
                <div className="status-error">OpenSearch status unavailable: {indexOverview.opensearch_error}</div>
              )}
              {indexOverviewError && <div className="status-error">{indexOverviewError}</div>}
            </div>
          </section>

          <section className="panel files-panel">
            <div className="files-panel-header">
              <h2>Files</h2>
              <button type="button" onClick={loadFilesMetadata} disabled={isLoadingFilesMetadata}>
                {isLoadingFilesMetadata ? 'Refreshing...' : 'Refresh list'}
              </button>
            </div>
            <p>
              This page lists all files currently in the data directory and sub-directories. Metadata columns are read from <code>index-files.md</code>.
            </p>


            {filesMetadataError && <div className="status-error">{filesMetadataError}</div>}

            <div className="files-table-wrap">
              <table className="files-table">
                <thead>
                  <tr>
                    <th>Path</th>
                    <th>Status</th>
                    <th>File Modified</th>
                    <th>Indexed At</th>
                    <th>Error</th>
                    <th>Last Occurred</th>
                  </tr>
                </thead>
                <tbody>
                  {filesMetadata.length === 0 ? (
                    <tr>
                      <td colSpan={7} className="files-empty">
                        {isLoadingFilesMetadata ? 'Loading file metadata...' : 'No files found in data/.'}
                      </td>
                    </tr>
                  ) : (
                    sortedFilesMetadata.map((file) => {
                      const relativePath = toRelativeDataPath(file.path)
                      const segments = relativePath.split('/').filter(Boolean)
                      const depth = Math.max(segments.length - 1, 0)
                      const fileName = segments[segments.length - 1] || relativePath
                      const parentPath = segments.slice(0, -1).join('/')
                      const treePrefix = `${'  '.repeat(depth)}${depth > 0 ? '|_ ' : ''}`

                      return (
                        <tr key={file.path}>
                          <td className="path-cell">
                            <div className="mono tree-line">
                              {`${treePrefix}`}
                              <a
                                href={`${API_BASE_URL}/data/${toRelativeDataPath(file.path)}`}
                                className="file-link"
                                target="_blank"
                                rel="noopener noreferrer"
                              >
                                {fileName}
                              </a>
                            </div>
                            <div className="tree-parent">{parentPath ? `data/${parentPath}/` : 'data/'}</div>
                          </td>
                          <td>
                            <span className={`file-status file-status-${file.status}`}>{file.status}</span>
                          </td>
                          <td>{file.file_modified_utc ? formatUtcShort(file.file_modified_utc) : '-'}</td>
                          <td>{file.indexed_at_utc ? formatUtcShort(file.indexed_at_utc) : '-'}</td>
                          <td className="file-error">{file.error || '-'}</td>
                          <td>{file.last_occurred_utc ? formatUtcShort(file.last_occurred_utc) : '-'}</td>
                        </tr>
                      )
                    })
                  )}
                </tbody>
              </table>
            </div>
          </section>
        </main>
      )}
    </div>
  )
}

export default App
