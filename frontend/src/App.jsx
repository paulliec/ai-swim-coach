import { useState, useRef, useEffect } from 'react'
import { SignIn, UserButton, useUser, SignedIn, SignedOut } from '@clerk/clerk-react'
import SessionHistory from './components/SessionHistory'

const STROKE_TYPES = ['freestyle', 'backstroke', 'breaststroke', 'butterfly']
// API base URL - uses environment variable for production, defaults to proxy for local dev
const API_BASE = import.meta.env.VITE_API_BASE || '/api/v1'

function App() {
  const { user, isLoaded } = useUser()
  
  // API Key - use from localStorage, then env var, then empty
  const defaultApiKey = localStorage.getItem('swimcoach_api_key') || import.meta.env.VITE_API_KEY || ''
  const [apiKey, setApiKey] = useState(defaultApiKey)
  const [showApiKeyInput, setShowApiKeyInput] = useState(!apiKey)
  
  // View state
  const [view, setView] = useState('upload') // 'upload' or 'history'
  
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
  const [analyzingLong, setAnalyzingLong] = useState(false)
  const [sessionId, setSessionId] = useState(null)
  const [analysis, setAnalysis] = useState(null)
  const [error, setError] = useState(null)
  
  // Chat
  const [messages, setMessages] = useState([])
  const [chatInput, setChatInput] = useState('')
  const [chatting, setChatting] = useState(false)
  
  const videoRef = useRef(null)
  const fileInputRef = useRef(null)
  const messagesEndRef = useRef(null)
  
  // Track anonymous session
  const [anonymousSessionId, setAnonymousSessionId] = useState(() => 
    localStorage.getItem('anonymous_session_id')
  )
  const [showSignupPrompt, setShowSignupPrompt] = useState(false)
  
  // Auto-scroll chat to bottom when new messages arrive
  useEffect(() => {
    if (messagesEndRef.current) {
      messagesEndRef.current.scrollIntoView({ behavior: 'smooth' })
    }
  }, [messages])
  
  // When user signs in, check if they have an anonymous session to claim
  useEffect(() => {
    if (user && anonymousSessionId && !sessionId) {
      setShowSignupPrompt(true)
    }
  }, [user, anonymousSessionId])
  
  // Claim anonymous session for authenticated user
  const claimAnonymousSession = async () => {
    if (!user || !anonymousSessionId) return
    
    try {
      const res = await fetch(`${API_BASE}/sessions/${anonymousSessionId}/claim`, {
        method: 'POST',
        headers: {
          'X-API-Key': apiKey,
          'X-User-Id': user.id,
          'Content-Type': 'application/json'
        }
      })
      
      if (res.ok) {
        // Session claimed successfully
        localStorage.removeItem('anonymous_session_id')
        setAnonymousSessionId(null)
        setShowSignupPrompt(false)
        
        // Load the claimed session
        setSessionId(anonymousSessionId)
      }
    } catch (err) {
      console.error('Failed to claim session:', err)
    }
  }
  
  // Dismiss signup prompt
  const dismissSignupPrompt = () => {
    setShowSignupPrompt(false)
    localStorage.removeItem('anonymous_session_id')
    setAnonymousSessionId(null)
  }

  // Save API key to localStorage
  const saveApiKey = () => {
    localStorage.setItem('swimcoach_api_key', apiKey)
    setShowApiKeyInput(false)
  }

  // Extract frames from video file
  // Helper: Detect if user is on mobile device
  const isMobileDevice = () => {
    // Check user agent
    const mobileRegex = /Android|webOS|iPhone|iPad|iPod|BlackBerry|IEMobile|Opera Mini/i
    const isMobileUA = mobileRegex.test(navigator.userAgent)
    
    // Check screen width (tablets and phones)
    const isSmallScreen = window.innerWidth <= 768
    
    return isMobileUA || isSmallScreen
  }

  const extractFrames = async (file) => {
    setExtracting(true)
    setError(null)
    
    const video = document.createElement('video')
    let objectUrl = null
    
    try {
      objectUrl = URL.createObjectURL(file)
      video.src = objectUrl
      video.muted = true
      video.playsInline = true  // Important for iOS
      video.preload = 'auto'
      
      // Detect mobile and adjust frame count
      const isMobile = isMobileDevice()
      const frameCount = isMobile ? 10 : 15  // Fewer frames on mobile for performance
      
      console.log(`Extracting ${frameCount} frames (${isMobile ? 'mobile' : 'desktop'} mode)`)
      
      // Wait for video to be fully loaded and ready to play
      // Use canplaythrough for better mobile compatibility
      await Promise.race([
        new Promise((resolve, reject) => {
          video.onloadedmetadata = () => {
            // After metadata loads, wait for canplaythrough
            video.oncanplaythrough = resolve
            video.onerror = reject
            video.load()  // Start loading
          }
          video.onerror = reject
        }),
        new Promise((_, reject) => 
          setTimeout(() => reject(new Error('Video loading timeout (30s)')), 30000)
        )
      ])
      
      const duration = video.duration
      
      if (!duration || duration === 0 || !isFinite(duration)) {
        throw new Error('Invalid video duration. The video file may be corrupted.')
      }
      
      const interval = duration / (frameCount + 1)
      const extractedFrames = []
      
      // Extract frames at uniform intervals
      for (let i = 1; i <= frameCount; i++) {
        const timestamp = i * interval
        
        // Seek to timestamp
        video.currentTime = timestamp
        
        // Wait for seek to complete with timeout
        await Promise.race([
          new Promise((resolve) => {
            video.onseeked = resolve
          }),
          new Promise((_, reject) => 
            setTimeout(() => reject(new Error(`Frame ${i} seek timeout`)), 5000)
          )
        ])
        
        // Ensure video has rendered the frame (important for iOS)
        await new Promise(resolve => setTimeout(resolve, 100))
        
        // Draw frame to canvas
        const canvas = document.createElement('canvas')
        
        // Use smaller dimensions on mobile to save memory
        const scale = isMobile ? 0.75 : 1
        canvas.width = video.videoWidth * scale
        canvas.height = video.videoHeight * scale
        
        const ctx = canvas.getContext('2d')
        ctx.drawImage(video, 0, 0, canvas.width, canvas.height)
        
        // Convert to blob with timeout
        const blob = await Promise.race([
          new Promise((resolve) => {
            canvas.toBlob(resolve, 'image/jpeg', 0.85)
          }),
          new Promise((_, reject) => 
            setTimeout(() => reject(new Error(`Frame ${i} blob conversion timeout`)), 5000)
          )
        ])
        
        if (!blob) {
          throw new Error(`Failed to create blob for frame ${i}`)
        }
        
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
      console.log(`Successfully extracted ${extractedFrames.length} frames`)
      
    } catch (err) {
      console.error('Frame extraction error:', err)
      
      // User-friendly error messages
      let errorMessage = 'Failed to extract frames from video. '
      
      if (err.message.includes('timeout')) {
        errorMessage += 'The video is taking too long to load. Try a smaller video file or check your connection.'
      } else if (err.message.includes('duration')) {
        errorMessage += 'The video file appears to be invalid or corrupted.'
      } else if (err.message.includes('blob')) {
        errorMessage += 'Your device may be low on memory. Try closing other apps or using a smaller video.'
      } else {
        errorMessage += err.message
      }
      
      setError(errorMessage)
      setFrames([])  // Clear any partial frames
      
    } finally {
      // Always cleanup
      if (objectUrl) {
        URL.revokeObjectURL(objectUrl)
      }
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
    setAnalyzing(false)
    setAnalyzingLong(false)
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
          'X-API-Key': apiKey,
          'X-User-Id': user?.id || 'anonymous'
        },
        body: formData
      })
      
      if (!uploadRes.ok) {
        let errorMessage = 'Upload failed'
        
        if (uploadRes.status === 429) {
          errorMessage = 'High demand right now. Please try again in a few minutes.'
        } else if (uploadRes.status === 413) {
          errorMessage = 'Upload size too large. Try uploading fewer frames or smaller images.'
        } else if (uploadRes.status === 500) {
          errorMessage = 'Something went wrong on our end. Please try again.'
        } else {
          try {
            const errData = await uploadRes.json()
            errorMessage = errData.detail || errorMessage
          } catch {
            // Couldn't parse error response
          }
        }
        
        throw new Error(errorMessage)
      }
      
      const uploadData = await uploadRes.json()
      setSessionId(uploadData.session_id)
      
      // Save to localStorage for anonymous users
      if (!user) {
        localStorage.setItem('anonymous_session_id', uploadData.session_id)
        setAnonymousSessionId(uploadData.session_id)
      }
      
      setUploading(false)
      
      // Step 2: Analyze frames
      setAnalyzing(true)
      
      // Set a timeout to show "taking longer" message
      const longWaitTimer = setTimeout(() => {
        setAnalyzingLong(true)
      }, 30000) // 30 seconds
      
      const analyzeRes = await fetch(`${API_BASE}/analysis/${uploadData.session_id}/analyze`, {
        method: 'POST',
        headers: {
          'X-API-Key': apiKey,
          'X-User-Id': user?.id || 'anonymous',
          'Content-Type': 'application/json'
        },
        body: JSON.stringify({
          stroke_type: strokeType,
          user_notes: userNotes
        })
      })
      
      // Clear the long wait timer
      clearTimeout(longWaitTimer)
      
      if (!analyzeRes.ok) {
        let errorMessage = 'Analysis failed'
        
        // Handle specific error codes
        if (analyzeRes.status === 429) {
          errorMessage = "You've reached your daily limit of 3 analyses. Come back tomorrow!"
        } else if (analyzeRes.status === 500) {
          errorMessage = 'Something went wrong on our end. Please try again.'
        } else {
          try {
            const errData = await analyzeRes.json()
            errorMessage = errData.detail || errorMessage
          } catch {
            // Couldn't parse error response
          }
        }
        
        throw new Error(errorMessage)
      }
      
      const analysisData = await analyzeRes.json()
      setAnalysis(analysisData)
      
      // Show signup prompt for anonymous users after successful analysis
      if (!user) {
        setShowSignupPrompt(true)
      }
      
    } catch (err) {
      setError(err.message)
      console.error(err)
    } finally {
      setUploading(false)
      setAnalyzing(false)
      setAnalyzingLong(false)
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
    
    // Add temporary "thinking" message
    const thinkingMsg = { role: 'assistant', content: 'Thinking...', isThinking: true }
    setMessages(prev => [...prev, thinkingMsg])
    
    setChatInput('')
    
    try {
      const res = await fetch(`${API_BASE}/sessions/${sessionId}/chat`, {
        method: 'POST',
        headers: {
          'X-API-Key': apiKey,
          'X-User-Id': user?.id || 'anonymous',
          'Content-Type': 'application/json'
        },
        body: JSON.stringify({
          message: chatInput
        })
      })
      
      if (!res.ok) {
        let errorMessage = 'Chat failed'
        
        if (res.status === 429) {
          errorMessage = 'High demand right now. Please try again in a few minutes.'
        } else if (res.status === 500) {
          errorMessage = 'Something went wrong. Please try again.'
        } else {
          try {
            const errData = await res.json()
            errorMessage = errData.detail || errorMessage
          } catch {
            // Couldn't parse error response
          }
        }
        
        throw new Error(errorMessage)
      }
      
      const data = await res.json()
      
      // Replace "thinking" message with actual response
      const assistantMsg = { role: 'assistant', content: data.assistant_message }
      setMessages(prev => prev.filter(m => !m.isThinking).concat(assistantMsg))
      
    } catch (err) {
      setError(err.message)
      console.error(err)
      // Remove "thinking" message on error
      setMessages(prev => prev.filter(m => !m.isThinking))
    } finally {
      setChatting(false)
    }
  }

  // Handle loading state
  if (!isLoaded) {
    return (
      <div className="min-h-screen bg-gradient-to-br from-blue-50 to-cyan-50 flex items-center justify-center">
        <p className="text-gray-600">Loading...</p>
      </div>
    )
  }

  // Handle selecting a session from history
  const handleSelectSession = async (sessionId) => {
    setSessionId(sessionId)
    setView('upload')
    
    // Fetch session details
    try {
      const res = await fetch(`${API_BASE}/sessions/${sessionId}`, {
        headers: {
          'X-API-Key': apiKey,
          'X-User-Id': user?.id || 'anonymous'
        }
      })
      
      if (res.ok) {
        const data = await res.json()
        if (data.summary) {
          setAnalysis({
            session_id: sessionId,
            summary: data.summary,
            feedback: [],
            frame_count: 0
          })
        }
        if (data.messages) {
          setMessages(data.messages.map(m => ({
            role: m.role,
            content: m.content
          })))
        }
      }
    } catch (err) {
      console.error('Failed to load session:', err)
    }
  }

  return (
    <div className="min-h-screen bg-gradient-to-br from-blue-50 to-cyan-50">
      <div className="container mx-auto px-4 py-8 max-w-6xl">
        {/* Header */}
        <header className="mb-8 flex justify-between items-start">
          <div>
            <h1 className="text-4xl font-bold text-gray-800 mb-2">
              🏊‍♂️ SwimCoach AI
            </h1>
            <p className="text-gray-600">
              Upload a swimming video and get personalized coaching feedback
            </p>
          </div>
          <SignedIn>
            <div className="flex items-center gap-4">
              <UserButton afterSignOutUrl="/" />
            </div>
          </SignedIn>
        </header>
        
        {/* Signup Prompt After Analysis (for anonymous users) */}
        {showSignupPrompt && !user && analysis && (
          <div className="bg-gradient-to-r from-blue-500 to-cyan-500 text-white rounded-lg shadow-lg p-6 mb-6">
            <div className="flex items-start justify-between">
              <div className="flex-1">
                <h3 className="text-xl font-bold mb-2">🎉 Great! Want to save your progress?</h3>
                <p className="mb-4">
                  Sign in to save this analysis and track your improvement over time. 
                  Your session history helps you see progress and revisit coaching feedback.
                </p>
                <div className="flex gap-3">
                  <button
                    onClick={() => {
                      setShowSignupPrompt(false)
                      // Scroll to show sign-in form (we'll add this below)
                      window.scrollTo({ top: 0, behavior: 'smooth' })
                    }}
                    className="px-6 py-2 bg-white text-blue-600 rounded-lg hover:bg-gray-100 font-semibold"
                  >
                    Sign In to Save
                  </button>
                  <button
                    onClick={dismissSignupPrompt}
                    className="px-6 py-2 bg-transparent border-2 border-white text-white rounded-lg hover:bg-white hover:bg-opacity-10"
                  >
                    Continue Without Saving
                  </button>
                </div>
              </div>
              <button
                onClick={dismissSignupPrompt}
                className="text-white hover:text-gray-200 ml-4"
              >
                ✕
              </button>
            </div>
          </div>
        )}
        
        {/* Claim Session Prompt (for newly signed-in users) */}
        {showSignupPrompt && user && anonymousSessionId && (
          <div className="bg-green-50 border border-green-200 rounded-lg p-6 mb-6">
            <h3 className="font-bold text-green-800 mb-2">Welcome back! 👋</h3>
            <p className="text-green-700 mb-4">
              You have an unsaved analysis from before you signed in. Would you like to save it to your account?
            </p>
            <div className="flex gap-3">
              <button
                onClick={claimAnonymousSession}
                className="px-6 py-2 bg-green-600 text-white rounded-lg hover:bg-green-700 font-semibold"
              >
                Yes, Save It
              </button>
              <button
                onClick={dismissSignupPrompt}
                className="px-6 py-2 bg-gray-200 text-gray-700 rounded-lg hover:bg-gray-300"
              >
                No Thanks
              </button>
            </div>
          </div>
        )}
        
        {/* Sign In Modal for Anonymous Users */}
        <SignedOut>
          {showSignupPrompt && (
            <div className="bg-white rounded-lg shadow-lg p-8 mb-6 max-w-md mx-auto">
              <SignIn routing="hash" />
            </div>
          )}
        </SignedOut>
        
        {/* Navigation Tabs (only for signed-in users) */}
        <SignedIn>
        <div className="mb-6 flex gap-2">
            <button
              onClick={() => setView('upload')}
              className={`px-4 py-2 rounded-lg font-medium transition-colors ${
                view === 'upload'
                  ? 'bg-blue-600 text-white'
                  : 'bg-white text-gray-700 hover:bg-gray-100'
              }`}
            >
              New Analysis
            </button>
            <button
              onClick={() => setView('history')}
              className={`px-4 py-2 rounded-lg font-medium transition-colors ${
                view === 'history'
                  ? 'bg-blue-600 text-white'
                  : 'bg-white text-gray-700 hover:bg-gray-100'
              }`}
            >
              My Sessions
            </button>
          </div>
          
        {/* Session History View (only for signed-in users) */}
        {view === 'history' && (
          <SessionHistory onSelectSession={handleSelectSession} />
        )}
        </SignedIn>
        
        {/* Upload/Analysis View (available to everyone) */}
        {(!user || view === 'upload') && (
            <>
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

              {/* Error Display */}
              {error && (
                <div className="bg-red-50 border border-red-200 rounded-lg p-4 mb-6 flex items-start justify-between">
                  <p className="text-red-700 flex-1">{error}</p>
                  <button
                    onClick={() => setError(null)}
                    className="text-red-700 hover:text-red-900 ml-4"
                    aria-label="Dismiss error"
                  >
                    ✕
                  </button>
                </div>
              )}
              
              {/* Loading State for Analysis */}
              {analyzing && (
                <div className="bg-blue-50 border border-blue-200 rounded-lg p-6 mb-6">
                  <div className="flex items-center justify-center mb-3">
                    <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-blue-600"></div>
                  </div>
                  <p className="text-center text-blue-900 font-medium">
                    {analyzingLong 
                      ? '⏱️ Taking longer than usual, please wait...' 
                      : '🤖 Analyzing your technique... this typically takes 10-15 seconds'}
                  </p>
                  {!analyzingLong && (
                    <p className="text-center text-blue-600 text-sm mt-2">
                      Our AI coach is reviewing your frames
                    </p>
                  )}
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
                      {uploading 
                        ? '⬆️ Uploading frames...' 
                        : analyzing 
                          ? analyzingLong 
                            ? '⏱️ Still analyzing, please wait...'
                            : '🤖 Analyzing with AI (10-15s)...'
                          : '🎯 Analyze My Technique'}
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
                          {msg.role === 'user' ? 'You' : 'Coach'}
                        </p>
                        <p className="text-gray-800 whitespace-pre-wrap">{msg.content}</p>
                      </div>
                    ))}
                    <div ref={messagesEndRef} />
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
            </>
          )}
      </div>
    </div>
  )
}

export default App