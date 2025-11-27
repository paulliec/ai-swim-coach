import { useState, useRef } from 'react'

const STROKE_TYPES = ['freestyle', 'backstroke', 'breaststroke', 'butterfly']
const API_BASE = '/api/v1'

function App() {
  // API Key
  const [apiKey, setApiKey] = useState(() => localStorage.getItem('swimcoach_api_key') || '')
  const [showApiKeyInput, setShowApiKeyInput] = useState(!apiKey)
  
  // Video & Frames
  const [videoFile, setVideoFile] = useState(null)
  const [frames, setFrames] = useState([])
  const [extracting, setExtracting] = useState(false)
  
  // Analysis Form
  const [strokeType, setStrokeType] = useState('freestyle')
  const [userNotes, setUserNotes] = useState('')
  
  // API State
  const [uploading, setUploading] = useState(false)
  const [analyzing, setAnalyzing] = useState(false)
  const [sessionId, setSessionId] = useState(null)
  const [analysis, setAnalysis] = useState(null)
  const [error, setError] = useState(null)
  
  // Chat
  const [messages, setMessages] = useState([])
  const [chatInput, setChatInput] = useState('')
  const [chatting, setChatting] = useState(false)
  
  const videoRef = useRef(null)
  const fileInputRef = useRef(null)

  // Save API key to localStorage
  const saveApiKey = () => {
    localStorage.setItem('swimcoach_api_key', apiKey)
    setShowApiKeyInput(false)
  }

  // Extract frames from video file
  const extractFrames = async (file) => {
    setExtracting(true)
    setError(null)
    
    try {
      const video = document.createElement('video')
      video.src = URL.createObjectURL(file)
      video.muted = true
      
      // Wait for video metadata to load
      await new Promise((resolve, reject) => {
        video.onloadedmetadata = resolve
        video.onerror = reject
      })
      
      const duration = video.duration
      const frameCount = 15
      const interval = duration / (frameCount + 1)
      
      const extractedFrames = []
      
      // Extract frames at uniform intervals
      for (let i = 1; i <= frameCount; i++) {
        const timestamp = i * interval
        
        // Seek to timestamp
        video.currentTime = timestamp
        
        // Wait for seek to complete
        await new Promise((resolve) => {
          video.onseeked = resolve
        })
        
        // Draw frame to canvas
        const canvas = document.createElement('canvas')
        canvas.width = video.videoWidth
        canvas.height = video.videoHeight
        const ctx = canvas.getContext('2d')
        ctx.drawImage(video, 0, 0)
        
        // Convert to blob
        const blob = await new Promise((resolve) => {
          canvas.toBlob(resolve, 'image/jpeg', 0.85)
        })
        
        // Create thumbnail
        const thumbnail = canvas.toDataURL('image/jpeg', 0.3)
        
        extractedFrames.push({
          blob,
          thumbnail,
          timestamp: timestamp.toFixed(2),
          number: i
        })
      }
      
      setFrames(extractedFrames)
      URL.revokeObjectURL(video.src)
    } catch (err) {
      setError(`Failed to extract frames: ${err.message}`)
      console.error(err)
    } finally {
      setExtracting(false)
    }
  }

  // Handle video file selection
  const handleVideoSelect = (e) => {
    const file = e.target.files[0]
    if (file) {
      setVideoFile(file)
      setFrames([])
      setAnalysis(null)
      setMessages([])
      setSessionId(null)
      extractFrames(file)
    }
  }

  // Upload frames and analyze
  const handleAnalyze = async () => {
    if (!apiKey) {
      setError('Please enter your API key')
      setShowApiKeyInput(true)
      return
    }
    
    if (frames.length === 0) {
      setError('Please select a video first')
      return
    }
    
    setUploading(true)
    setError(null)
    
    try {
      // Step 1: Upload frames
      const formData = new FormData()
      frames.forEach((frame) => {
        formData.append('frames', frame.blob, `frame_${frame.number}.jpg`)
      })
      formData.append('stroke_type', strokeType)
      formData.append('user_notes', userNotes)
      
      const uploadRes = await fetch(`${API_BASE}/analysis/upload`, {
        method: 'POST',
        headers: {
          'X-API-Key': apiKey
        },
        body: formData
      })
      
      if (!uploadRes.ok) {
        const errData = await uploadRes.json()
        throw new Error(errData.detail || 'Upload failed')
      }
      
      const uploadData = await uploadRes.json()
      setSessionId(uploadData.session_id)
      setUploading(false)
      
      // Step 2: Analyze frames
      setAnalyzing(true)
      
      const analyzeRes = await fetch(`${API_BASE}/analysis/${uploadData.session_id}/analyze`, {
        method: 'POST',
        headers: {
          'X-API-Key': apiKey,
          'Content-Type': 'application/json'
        },
        body: JSON.stringify({
          stroke_type: strokeType,
          user_notes: userNotes
        })
      })
      
      if (!analyzeRes.ok) {
        const errData = await analyzeRes.json()
        throw new Error(errData.detail || 'Analysis failed')
      }
      
      const analysisData = await analyzeRes.json()
      setAnalysis(analysisData)
      
    } catch (err) {
      setError(err.message)
      console.error(err)
    } finally {
      setUploading(false)
      setAnalyzing(false)
    }
  }

  // Send chat message
  const handleChat = async (e) => {
    e.preventDefault()
    
    if (!chatInput.trim() || !sessionId) return
    
    setChatting(true)
    setError(null)
    
    // Add user message immediately
    const userMsg = { role: 'user', content: chatInput }
    setMessages(prev => [...prev, userMsg])
    setChatInput('')
    
    try {
      const res = await fetch(`${API_BASE}/sessions/${sessionId}/chat`, {
        method: 'POST',
        headers: {
          'X-API-Key': apiKey,
          'Content-Type': 'application/json'
        },
        body: JSON.stringify({
          message: chatInput
        })
      })
      
      if (!res.ok) {
        const errData = await res.json()
        throw new Error(errData.detail || 'Chat failed')
      }
      
      const data = await res.json()
      
      // Add assistant response
      const assistantMsg = { role: 'assistant', content: data.assistant_message }
      setMessages(prev => [...prev, assistantMsg])
      
    } catch (err) {
      setError(err.message)
      console.error(err)
    } finally {
      setChatting(false)
    }
  }

  return (
    <div className="min-h-screen bg-gradient-to-br from-blue-50 to-cyan-50">
      <div className="container mx-auto px-4 py-8 max-w-6xl">
        {/* Header */}
        <header className="mb-8">
          <h1 className="text-4xl font-bold text-gray-800 mb-2">
            🏊‍♂️ SwimCoach AI
          </h1>
          <p className="text-gray-600">
            Upload a swimming video and get personalized coaching feedback
          </p>
        </header>

        {/* API Key Section */}
        {showApiKeyInput && (
          <div className="bg-yellow-50 border border-yellow-200 rounded-lg p-4 mb-6">
            <h3 className="font-semibold mb-2">🔑 API Key Required</h3>
            <div className="flex gap-2">
              <input
                type="password"
                value={apiKey}
                onChange={(e) => setApiKey(e.target.value)}
                placeholder="Enter your API key (e.g., dev-key-1)"
                className="flex-1 px-3 py-2 border rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500"
              />
              <button
                onClick={saveApiKey}
                className="px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700"
              >
                Save
              </button>
            </div>
            <p className="text-sm text-gray-600 mt-2">
              For testing, use: <code className="bg-gray-200 px-2 py-1 rounded">dev-key-1</code>
            </p>
          </div>
        )}

        {!showApiKeyInput && (
          <div className="bg-green-50 border border-green-200 rounded-lg p-3 mb-6 flex items-center justify-between">
            <span className="text-green-700">✓ API key configured</span>
            <button
              onClick={() => setShowApiKeyInput(true)}
              className="text-sm text-blue-600 hover:underline"
            >
              Change
            </button>
          </div>
        )}

        {/* Error Display */}
        {error && (
          <div className="bg-red-50 border border-red-200 rounded-lg p-4 mb-6">
            <p className="text-red-700">{error}</p>
          </div>
        )}

        {/* Video Upload Section */}
        <div className="bg-white rounded-lg shadow-lg p-6 mb-6">
          <h2 className="text-2xl font-semibold mb-4">1. Select Video</h2>
          
          <input
            ref={fileInputRef}
            type="file"
            accept="video/*"
            onChange={handleVideoSelect}
            className="hidden"
          />
          
          <button
            onClick={() => fileInputRef.current?.click()}
            disabled={extracting}
            className="w-full px-6 py-4 bg-blue-600 text-white rounded-lg hover:bg-blue-700 disabled:bg-gray-400 disabled:cursor-not-allowed transition-colors font-semibold"
          >
            {extracting ? 'Extracting frames...' : videoFile ? `Change Video (${videoFile.name})` : 'Choose Video File'}
          </button>

          {/* Frame Previews */}
          {frames.length > 0 && (
            <div className="mt-6">
              <h3 className="font-semibold mb-3">
                Extracted {frames.length} frames
              </h3>
              <div className="grid grid-cols-5 gap-2">
                {frames.map((frame) => (
                  <div key={frame.number} className="relative">
                    <img
                      src={frame.thumbnail}
                      alt={`Frame ${frame.number}`}
                      className="w-full h-auto rounded border border-gray-300"
                    />
                    <span className="absolute bottom-1 right-1 bg-black bg-opacity-70 text-white text-xs px-1 rounded">
                      {frame.timestamp}s
                    </span>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>

        {/* Analysis Form */}
        {frames.length > 0 && !analysis && (
          <div className="bg-white rounded-lg shadow-lg p-6 mb-6">
            <h2 className="text-2xl font-semibold mb-4">2. Analysis Settings</h2>
            
            <div className="space-y-4">
              <div>
                <label className="block font-medium mb-2">Stroke Type</label>
                <select
                  value={strokeType}
                  onChange={(e) => setStrokeType(e.target.value)}
                  className="w-full px-3 py-2 border rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500"
                >
                  {STROKE_TYPES.map(type => (
                    <option key={type} value={type}>
                      {type.charAt(0).toUpperCase() + type.slice(1)}
                    </option>
                  ))}
                </select>
              </div>

              <div>
                <label className="block font-medium mb-2">Notes (Optional)</label>
                <textarea
                  value={userNotes}
                  onChange={(e) => setUserNotes(e.target.value)}
                  placeholder="Any specific areas you want feedback on?"
                  rows={3}
                  className="w-full px-3 py-2 border rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500"
                />
              </div>

              <button
                onClick={handleAnalyze}
                disabled={uploading || analyzing}
                className="w-full px-6 py-3 bg-green-600 text-white rounded-lg hover:bg-green-700 disabled:bg-gray-400 disabled:cursor-not-allowed transition-colors font-semibold text-lg"
              >
                {uploading ? 'Uploading frames...' : analyzing ? 'Analyzing with AI...' : '🎯 Analyze My Technique'}
              </button>
            </div>
          </div>
        )}

        {/* Analysis Results */}
        {analysis && (
          <div className="bg-white rounded-lg shadow-lg p-6 mb-6">
            <h2 className="text-2xl font-semibold mb-4">📊 Coaching Feedback</h2>
            
            <div className="mb-6">
              <h3 className="font-semibold text-lg mb-2">Summary</h3>
              <p className="text-gray-700 whitespace-pre-wrap">{analysis.summary}</p>
            </div>

            {analysis.feedback && analysis.feedback.length > 0 && (
              <div>
                <h3 className="font-semibold text-lg mb-3">Detailed Feedback</h3>
                <div className="space-y-4">
                  {analysis.feedback.map((item, idx) => (
                    <div key={idx} className="border-l-4 border-blue-500 pl-4 py-2">
                      <div className="flex items-center gap-2 mb-1">
                        <span className={`text-xs font-semibold px-2 py-1 rounded ${
                          item.priority === 'primary' ? 'bg-red-100 text-red-700' :
                          item.priority === 'secondary' ? 'bg-yellow-100 text-yellow-700' :
                          'bg-gray-100 text-gray-700'
                        }`}>
                          {item.priority.toUpperCase()}
                        </span>
                        <span className="text-sm text-gray-600">{item.category}</span>
                      </div>
                      <p className="font-medium text-gray-800 mb-1">{item.observation}</p>
                      <p className="text-gray-700 mb-2">{item.recommendation}</p>
                      {item.drill_suggestions && item.drill_suggestions.length > 0 && (
                        <div className="text-sm">
                          <span className="font-medium">Drills: </span>
                          <span className="text-gray-600">{item.drill_suggestions.join(', ')}</span>
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        )}

        {/* Chat Interface */}
        {analysis && (
          <div className="bg-white rounded-lg shadow-lg p-6">
            <h2 className="text-2xl font-semibold mb-4">💬 Ask Follow-up Questions</h2>
            
            {/* Messages */}
            <div className="space-y-4 mb-4 max-h-96 overflow-y-auto">
              {messages.map((msg, idx) => (
                <div
                  key={idx}
                  className={`p-3 rounded-lg ${
                    msg.role === 'user'
                      ? 'bg-blue-100 ml-12'
                      : 'bg-gray-100 mr-12'
                  }`}
                >
                  <p className="text-sm font-semibold mb-1">
                    {msg.role === 'user' ? 'You' : '🏊‍♂️ Coach'}
                  </p>
                  <p className="text-gray-800 whitespace-pre-wrap">{msg.content}</p>
                </div>
              ))}
            </div>

            {/* Chat Input */}
            <form onSubmit={handleChat} className="flex gap-2">
              <input
                type="text"
                value={chatInput}
                onChange={(e) => setChatInput(e.target.value)}
                placeholder="Ask about your technique..."
                disabled={chatting}
                className="flex-1 px-4 py-2 border rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500 disabled:bg-gray-100"
              />
              <button
                type="submit"
                disabled={chatting || !chatInput.trim()}
                className="px-6 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 disabled:bg-gray-400 disabled:cursor-not-allowed transition-colors"
              >
                {chatting ? 'Sending...' : 'Send'}
              </button>
            </form>
          </div>
        )}

        {/* Footer */}
        <footer className="mt-12 text-center text-gray-600 text-sm">
          <p>SwimCoach AI • Powered by Claude • {frames.length > 0 && `${frames.length} frames extracted`}</p>
        </footer>
      </div>
    </div>
  )
}

export default App

