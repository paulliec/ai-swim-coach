import { useState, useEffect } from 'react'
import { useUser } from '@clerk/clerk-react'

const API_BASE = '/api/v1'

function SessionHistory({ onSelectSession }) {
  const { user } = useUser()
  const [sessions, setSessions] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const apiKey = localStorage.getItem('swimcoach_api_key')

  useEffect(() => {
    if (user && apiKey) {
      fetchSessions()
    }
  }, [user, apiKey])

  const fetchSessions = async () => {
    setLoading(true)
    setError(null)
    
    try {
      const res = await fetch(`${API_BASE}/users/me/sessions`, {
        headers: {
          'X-API-Key': apiKey,
          'X-User-Id': user.id
        }
      })
      
      if (!res.ok) {
        throw new Error('Failed to fetch sessions')
      }
      
      const data = await res.json()
      setSessions(data.sessions || [])
    } catch (err) {
      setError(err.message)
      console.error(err)
    } finally {
      setLoading(false)
    }
  }

  if (loading) {
    return (
      <div className="bg-white rounded-lg shadow-lg p-6">
        <h2 className="text-2xl font-semibold mb-4">My Sessions</h2>
        <p className="text-gray-600">Loading your sessions...</p>
      </div>
    )
  }

  if (error) {
    return (
      <div className="bg-white rounded-lg shadow-lg p-6">
        <h2 className="text-2xl font-semibold mb-4">My Sessions</h2>
        <p className="text-red-600">{error}</p>
      </div>
    )
  }

  if (sessions.length === 0) {
    return (
      <div className="bg-white rounded-lg shadow-lg p-6">
        <h2 className="text-2xl font-semibold mb-4">My Sessions</h2>
        <p className="text-gray-600">No sessions yet. Upload a video to get started!</p>
      </div>
    )
  }

  return (
    <div className="bg-white rounded-lg shadow-lg p-6">
      <h2 className="text-2xl font-semibold mb-4">My Sessions</h2>
      
      <div className="space-y-3">
        {sessions.map((session) => (
          <button
            key={session.session_id}
            onClick={() => onSelectSession(session.session_id)}
            className="w-full text-left p-4 border rounded-lg hover:border-blue-500 hover:bg-blue-50 transition-colors"
          >
            <div className="flex justify-between items-start mb-2">
              <span className="font-semibold text-gray-800">
                {session.stroke_type || 'Unknown stroke'}
              </span>
              <span className="text-sm text-gray-500">
                {new Date(session.created_at).toLocaleDateString()}
              </span>
            </div>
            
            {session.summary && (
              <p className="text-sm text-gray-600 line-clamp-2">
                {session.summary.substring(0, 150)}...
              </p>
            )}
            
            <div className="mt-2 flex items-center gap-2 text-xs text-gray-500">
              {session.message_count > 0 && (
                <span>💬 {session.message_count} messages</span>
              )}
              {session.frame_count && (
                <span>🎞️ {session.frame_count} frames</span>
              )}
            </div>
          </button>
        ))}
      </div>
    </div>
  )
}

export default SessionHistory

