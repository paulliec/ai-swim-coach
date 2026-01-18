import { useState, useRef, useEffect } from 'react'
import { SignIn, UserButton, useUser, SignedIn, SignedOut } from '@clerk/clerk-react'
import SessionHistory from './components/SessionHistory'

const STROKE_TYPES = ['freestyle', 'backstroke', 'breaststroke', 'butterfly']
// API base URL - uses environment variable for production, defaults to proxy for local dev
const API_BASE = import.meta.env.VITE_API_BASE || '/api/v1'

// Analysis modes
const ANALYSIS_MODES = {
  FRAMES: 'frames',    // Client-side frame extraction (original)
  VIDEO: 'video',      // Server-side video processing (agentic)
}

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
  
  // Frame extraction settings
  const [framesPerSecond, setFramesPerSecond] = useState(1)
  const [videoDuration, setVideoDuration] = useState(0)
  
  // Analysis mode - frames (client) or video (server/agentic)
  const [analysisMode, setAnalysisMode] = useState(ANALYSIS_MODES.FRAMES)
  const [videoUploading, setVideoUploading] = useState(false)
  const [videoInfo, setVideoInfo] = useState(null)
  const [agenticProgress, setAgenticProgress] = useState('')
  
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
    const mobileRegex = /Android|webOS|iPhone|iPad|iPod|BlackBerry|IEMobile|Opera Mini/i
    return mobileRegex.test(navigator.userAgent) || window.innerWidth <= 768
  }

  const extractFrames = async (file, fps = framesPerSecond) => {
    setExtracting(true)
    setError(null)
    
    let objectUrl = null
    const video = document.createElement('video')
    
    try {
      // Create object URL and set video properties
      objectUrl = URL.createObjectURL(file)
      video.src = objectUrl
      video.muted = true
      video.playsInline = true  // Important for iOS
      
      // Wait for video metadata to load (with timeout)
      await Promise.race([
        new Promise((resolve, reject) => {
          video.onloadedmetadata = resolve
          video.onerror = reject
        }),
        new Promise((_, reject) => 
          setTimeout(() => reject(new Error('Video loading timeout')), 30000)
        )
      ])
      
      const duration = video.duration
      
      if (!duration || !isFinite(duration)) {
        throw new Error('Invalid video duration')
      }
      
      // Store duration for UI
      setVideoDuration(duration)
      
      // Calculate frame count based on FPS, capped at 60
      const isMobile = isMobileDevice()
      const maxFrames = isMobile ? 40 : 60
      let frameCount = Math.round(duration * fps)
      frameCount = Math.max(5, Math.min(frameCount, maxFrames)) // Min 5, max 40-60
      
      console.log(`Extracting ${frameCount} frames at ${fps} FPS from ${duration.toFixed(1)}s video (${isMobile ? 'mobile' : 'desktop'} mode)`)
      
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
        
        // Small delay for iOS to render the frame
        if (isMobileDevice()) {
          await new Promise(resolve => setTimeout(resolve, 100))
        }
        
        // Draw frame to canvas
        const canvas = document.createElement('canvas')
        
        // Use smaller dimensions on mobile
        const scale = isMobileDevice() ? 0.75 : 1
        canvas.width = video.videoWidth * scale
        canvas.height = video.videoHeight * scale
        
        const ctx = canvas.getContext('2d')
        ctx.drawImage(video, 0, 0, canvas.width, canvas.height)
        
        // Convert to blob
        const blob = await new Promise((resolve) => {
          canvas.toBlob(resolve, 'image/jpeg', 0.85)
        })
        
        if (!blob) {
          throw new Error(`Failed to create frame ${i}`)
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
      
      let errorMessage = 'Failed to extract frames from video. '
      
      if (err.message.includes('timeout')) {
        errorMessage += 'The video took too long to load. Try a smaller video file.'
      } else if (err.message.includes('Invalid video')) {
        errorMessage += 'The video file may be corrupted or in an unsupported format.'
      } else {
        errorMessage += err.message
      }
      
      setError(errorMessage)
      setFrames([])
      
    } finally {
      // Cleanup: revoke object URL after extraction is complete
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
      }, 60000) // 60 seconds
      
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

  // Upload video for server-side processing (agentic mode)
  const handleVideoUpload = async () => {
    if (!apiKey) {
      setError('Please enter your API key')
      setShowApiKeyInput(true)
      return
    }
    
    if (!videoFile) {
      setError('Please select a video first')
      return
    }
    
    setVideoUploading(true)
    setError(null)
    setAgenticProgress('Uploading video...')
    
    try {
      // Step 1: Upload video
      const formData = new FormData()
      formData.append('video', videoFile)
      
      const uploadRes = await fetch(`${API_BASE}/video/upload`, {
        method: 'POST',
        headers: {
          'X-API-Key': apiKey,
          'X-User-Id': user?.id || 'anonymous'
        },
        body: formData
      })
      
      if (!uploadRes.ok) {
        const errData = await uploadRes.json().catch(() => ({}))
        throw new Error(errData.detail || 'Video upload failed')
      }
      
      const uploadData = await uploadRes.json()
      setSessionId(uploadData.session_id)
      setVideoInfo({
        duration: uploadData.duration_seconds,
        resolution: uploadData.resolution,
        fps: uploadData.fps
      })
      
      // Step 2: Run agentic analysis
      setAgenticProgress('Starting AI analysis...')
      setAnalyzing(true)
      
      const analyzeRes = await fetch(`${API_BASE}/video/${uploadData.session_id}/analyze`, {
        method: 'POST',
        headers: {
          'X-API-Key': apiKey,
          'X-User-Id': user?.id || 'anonymous',
          'Content-Type': 'application/json'
        },
        body: JSON.stringify({
          stroke_type: strokeType,
          user_notes: userNotes,
          initial_fps: 0.5,
          max_iterations: 3
        })
      })
      
      if (!analyzeRes.ok) {
        const errData = await analyzeRes.json().catch(() => ({}))
        if (analyzeRes.status === 429) {
          throw new Error("You've reached your daily limit. Come back tomorrow!")
        }
        throw new Error(errData.detail || 'Analysis failed')
      }
      
      const analysisData = await analyzeRes.json()
      
      // Convert agentic response to display format
      setAnalysis({
        session_id: analysisData.session_id,
        stroke_type: analysisData.stroke_type,
        summary: analysisData.summary,
        strengths: analysisData.strengths,
        timestamp_feedback: analysisData.timestamp_feedback,
        drills: analysisData.drills,
        frame_count: analysisData.total_frames_analyzed,
        iterations: analysisData.iterations_used,
        analysis_progress: analysisData.analysis_progress || [],  // progress from each iteration
        partial: analysisData.partial || false,  // true if analysis was interrupted
        can_resume: analysisData.can_resume || false,  // true if can resume from where it left off
        isAgentic: true  // Flag to render timestamp UI
      })
      
      if (!user) {
        localStorage.setItem('anonymous_session_id', analysisData.session_id)
        setAnonymousSessionId(analysisData.session_id)
        setShowSignupPrompt(true)
      }
      
    } catch (err) {
      setError(err.message)
      console.error('Video analysis error:', err)
    } finally {
      setVideoUploading(false)
      setAnalyzing(false)
      setAgenticProgress('')
    }
  }

  // Resume interrupted analysis
  const handleResumeAnalysis = async () => {
    if (!apiKey) {
      setError('Please enter your API key')
      setShowApiKeyInput(true)
      return
    }
    
    if (!sessionId && !analysis?.session_id) {
      setError('No session to resume')
      return
    }
    
    const resumeSessionId = sessionId || analysis?.session_id
    
    setAnalyzing(true)
    setError(null)
    setAgenticProgress('Resuming analysis...')
    
    try {
      const resumeRes = await fetch(`${API_BASE}/video/${resumeSessionId}/resume`, {
        method: 'POST',
        headers: {
          'X-API-Key': apiKey,
          'X-User-Id': user?.id || 'anonymous',
          'Content-Type': 'application/json'
        }
      })
      
      if (!resumeRes.ok) {
        const errData = await resumeRes.json().catch(() => ({}))
        throw new Error(errData.detail || 'Resume failed')
      }
      
      const analysisData = await resumeRes.json()
      
      setAnalysis({
        session_id: analysisData.session_id,
        stroke_type: analysisData.stroke_type,
        summary: analysisData.summary,
        strengths: analysisData.strengths,
        timestamp_feedback: analysisData.timestamp_feedback,
        drills: analysisData.drills,
        frame_count: analysisData.total_frames_analyzed,
        iterations: analysisData.iterations_used,
        analysis_progress: analysisData.analysis_progress || [],
        partial: analysisData.partial || false,
        can_resume: analysisData.can_resume || false,
        isAgentic: true
      })
      
    } catch (err) {
      setError(err.message)
      console.error('Resume analysis error:', err)
    } finally {
      setAnalyzing(false)
      setAgenticProgress('')
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
                      : `🤖 Analyzing your technique... this typically takes 30-60 seconds`}
                  </p>
                  {!analyzingLong && (
                    <p className="text-center text-blue-600 text-sm mt-2">
                      Our AI coach is reviewing {frames.length} frames
                    </p>
                  )}
                </div>
              )}

              {/* Video Upload Section */}
              <div className="bg-white rounded-lg shadow-lg p-6 mb-6">
                <h2 className="text-2xl font-semibold mb-4">1. Select Video</h2>
                
                {/* Analysis Mode Toggle */}
                <div className="mb-4 p-3 bg-gray-50 rounded-lg">
                  <label className="block text-sm font-medium text-gray-700 mb-2">Analysis Mode</label>
                  <div className="flex gap-2">
                    <button
                      onClick={() => setAnalysisMode(ANALYSIS_MODES.FRAMES)}
                      className={`flex-1 px-4 py-2 rounded-lg text-sm font-medium transition-colors ${
                        analysisMode === ANALYSIS_MODES.FRAMES
                          ? 'bg-blue-600 text-white'
                          : 'bg-white text-gray-700 border border-gray-300 hover:bg-gray-100'
                      }`}
                    >
                      📸 Frame Mode
                      <span className="block text-xs opacity-75">Browser extracts frames</span>
                    </button>
                    <button
                      onClick={() => setAnalysisMode(ANALYSIS_MODES.VIDEO)}
                      className={`flex-1 px-4 py-2 rounded-lg text-sm font-medium transition-colors ${
                        analysisMode === ANALYSIS_MODES.VIDEO
                          ? 'bg-purple-600 text-white'
                          : 'bg-white text-gray-700 border border-gray-300 hover:bg-gray-100'
                      }`}
                    >
                      🎬 Video Mode
                      <span className="block text-xs opacity-75">AI requests specific frames</span>
                    </button>
                  </div>
                  <p className="text-xs text-gray-500 mt-2">
                    {analysisMode === ANALYSIS_MODES.VIDEO 
                      ? '✨ Video Mode: AI analyzes, requests more frames from specific moments, gives timestamp-linked feedback'
                      : 'Frame Mode: Works on all devices. Good for quick analysis.'}
                  </p>
                </div>
                
                <input
                  ref={fileInputRef}
                  type="file"
                  accept="video/*"
                  onChange={handleVideoSelect}
                  className="hidden"
                />
                
                <button
                  onClick={() => fileInputRef.current?.click()}
                  disabled={extracting || videoUploading}
                  className="w-full px-6 py-4 bg-blue-600 text-white rounded-lg hover:bg-blue-700 disabled:bg-gray-400 disabled:cursor-not-allowed transition-colors font-semibold"
                >
                  {extracting ? 'Extracting frames...' : videoFile ? `Change Video (${videoFile.name})` : 'Choose Video File'}
                </button>

                {/* Video Info (for video mode) */}
                {videoFile && analysisMode === ANALYSIS_MODES.VIDEO && (
                  <div className="mt-4 p-3 bg-purple-50 rounded-lg">
                    <p className="text-sm text-purple-800">
                      <strong>📹 Video selected:</strong> {videoFile.name}
                      {videoInfo && (
                        <span className="ml-2">
                          ({videoInfo.duration?.toFixed(1)}s, {videoInfo.resolution})
                        </span>
                      )}
                    </p>
                    <p className="text-xs text-purple-600 mt-1">
                      Video will be uploaded to server for AI-guided frame extraction
                    </p>
                  </div>
                )}

                {/* Frame Previews (for frame mode) */}
                {frames.length > 0 && analysisMode === ANALYSIS_MODES.FRAMES && (
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
              {((frames.length > 0 && analysisMode === ANALYSIS_MODES.FRAMES) || 
                (videoFile && analysisMode === ANALYSIS_MODES.VIDEO)) && !analysis && (
                <div className="bg-white rounded-lg shadow-lg p-6 mb-6">
                  <h2 className="text-2xl font-semibold mb-4">2. Analysis Settings</h2>
                  
                  <div className="space-y-4">
                    {/* FPS Selector (only for frame mode) */}
                    {analysisMode === ANALYSIS_MODES.FRAMES && (
                      <div>
                        <label className="block font-medium mb-2">
                          Frame Rate: {framesPerSecond} FPS
                          <span className="text-gray-500 font-normal ml-2">
                            ({Math.max(5, Math.min(Math.round(videoDuration * framesPerSecond), isMobileDevice() ? 40 : 60))} frames)
                          </span>
                        </label>
                        <input
                          type="range"
                          min="0.5"
                          max="3"
                          step="0.5"
                          value={framesPerSecond}
                          onChange={(e) => {
                            const newFps = parseFloat(e.target.value)
                            setFramesPerSecond(newFps)
                            if (videoFile) {
                              extractFrames(videoFile, newFps)
                            }
                          }}
                          className="w-full h-2 bg-gray-200 rounded-lg appearance-none cursor-pointer"
                        />
                        <div className="flex justify-between text-xs text-gray-500 mt-1">
                          <span>0.5 (fewer frames)</span>
                          <span>3 (more detail)</span>
                        </div>
                        <p className="text-sm text-gray-600 mt-2">
                          Higher FPS captures more detail for fast movements (catch, entry). Lower FPS is faster to analyze.
                        </p>
                      </div>
                    )}
                    
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

                    {/* Analyze Button - different for each mode */}
                    {analysisMode === ANALYSIS_MODES.FRAMES ? (
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
                              : '🤖 Analyzing with AI (30-60s)...'
                            : '🎯 Analyze My Technique'}
                      </button>
                    ) : (
                      <button
                        onClick={handleVideoUpload}
                        disabled={videoUploading || analyzing}
                        className="w-full px-6 py-3 bg-purple-600 text-white rounded-lg hover:bg-purple-700 disabled:bg-gray-400 disabled:cursor-not-allowed transition-colors font-semibold text-lg"
                      >
                        {videoUploading || analyzing
                          ? `🤖 ${agenticProgress || 'Processing...'}`
                          : '🎬 Analyze with AI Agent'}
                      </button>
                    )}
                    
                    {analysisMode === ANALYSIS_MODES.VIDEO && (
                      <p className="text-xs text-center text-gray-500">
                        AI will review video, request more frames from key moments, and provide timestamp-linked feedback
                      </p>
                    )}
                  </div>
                </div>
              )}

              {/* Analysis Results */}
              {analysis && (
                <div className="bg-white rounded-lg shadow-lg p-6 mb-6">
                  <h2 className="text-2xl font-semibold mb-4">
                    📊 Coaching Feedback
                    {analysis.isAgentic && (
                      <span className="ml-2 text-sm font-normal text-purple-600 bg-purple-50 px-2 py-1 rounded">
                        🤖 Agentic Analysis
                      </span>
                    )}
                  </h2>
                  
                  {/* Partial Results Banner */}
                  {analysis.partial && (
                    <div className="mb-4 p-4 bg-yellow-50 border border-yellow-200 rounded-lg">
                      <div className="flex items-start gap-3">
                        <span className="text-2xl">⚠️</span>
                        <div className="flex-1">
                          <p className="font-semibold text-yellow-800 mb-1">Partial Analysis</p>
                          <p className="text-sm text-yellow-700 mb-2">
                            {analysis.can_resume 
                              ? "The AI hit a rate limit. Your progress is saved! Wait 1-2 minutes then click Resume to continue from where it left off."
                              : "The AI hit a rate limit during analysis. Here's what it observed so far. Try again in 1-2 minutes for the complete analysis."}
                          </p>
                          <div className="flex gap-2">
                            {analysis.can_resume && (
                              <button
                                onClick={handleResumeAnalysis}
                                disabled={analyzing}
                                className="px-4 py-2 bg-green-600 text-white rounded-lg hover:bg-green-700 disabled:bg-gray-400 text-sm font-medium"
                              >
                                {analyzing ? '⏳ Resuming...' : '▶️ Resume Analysis'}
                              </button>
                            )}
                            <button
                              onClick={() => {
                                setAnalysis(null)
                                setError(null)
                              }}
                              className="px-4 py-2 bg-yellow-600 text-white rounded-lg hover:bg-yellow-700 text-sm font-medium"
                            >
                              🔄 Start Over
                            </button>
                          </div>
                        </div>
                      </div>
                    </div>
                  )}
                  
                  {/* Analysis metadata */}
                  {analysis.isAgentic && (
                    <div className="mb-4 p-3 bg-purple-50 rounded-lg text-sm text-purple-800">
                      <p>
                        AI analyzed <strong>{analysis.frame_count}</strong> frames over <strong>{analysis.iterations}</strong> pass{analysis.iterations > 1 ? 'es' : ''}
                      </p>
                    </div>
                  )}
                  
                  {/* Analysis Progress (shows what AI observed at each iteration) */}
                  {analysis.analysis_progress && analysis.analysis_progress.length > 0 && (
                    <details className="mb-6 bg-gray-50 rounded-lg p-3">
                      <summary className="cursor-pointer font-medium text-gray-700 hover:text-gray-900">
                        📋 Analysis Progress ({analysis.analysis_progress.length} passes) - click to expand
                      </summary>
                      <div className="mt-3 space-y-3">
                        {analysis.analysis_progress.map((progress, idx) => (
                          <div key={idx} className="border-l-2 border-gray-300 pl-3 py-1">
                            <div className="flex items-center gap-2 mb-1">
                              <span className="text-xs font-semibold bg-gray-200 text-gray-700 px-2 py-0.5 rounded">
                                Pass {progress.iteration}
                              </span>
                              <span className="text-xs text-gray-500">
                                {progress.frames_reviewed} frames reviewed
                              </span>
                            </div>
                            <p className="text-sm text-gray-700 mb-1">{progress.observations}</p>
                            {progress.areas_requested && progress.areas_requested.length > 0 && (
                              <div className="text-xs text-purple-600">
                                <span className="font-medium">Requested closer look:</span>{' '}
                                {progress.areas_requested.join(' | ')}
                              </div>
                            )}
                          </div>
                        ))}
                      </div>
                    </details>
                  )}
                  
                  <div className="mb-6">
                    <h3 className="font-semibold text-lg mb-2">Summary</h3>
                    <p className="text-gray-700 whitespace-pre-wrap">{analysis.summary}</p>
                  </div>
                  
                  {/* Strengths (from agentic analysis) */}
                  {analysis.strengths && analysis.strengths.length > 0 && (
                    <div className="mb-6">
                      <h3 className="font-semibold text-lg mb-2">✨ Strengths</h3>
                      <ul className="list-disc pl-5 text-gray-700 space-y-1">
                        {analysis.strengths.map((strength, idx) => (
                          <li key={idx}>{strength}</li>
                        ))}
                      </ul>
                    </div>
                  )}

                  {/* Timestamp-linked Feedback (from agentic analysis) */}
                  {analysis.timestamp_feedback && analysis.timestamp_feedback.length > 0 && (
                    <div className="mb-6">
                      <h3 className="font-semibold text-lg mb-3">⏱️ Timestamp Feedback</h3>
                      <div className="space-y-4">
                        {analysis.timestamp_feedback.map((item, idx) => (
                          <div key={idx} className="border-l-4 border-purple-500 pl-4 py-2 bg-purple-50 rounded-r-lg">
                            <div className="flex items-center gap-2 mb-1">
                              <span className="text-sm font-mono bg-purple-200 text-purple-800 px-2 py-0.5 rounded">
                                {item.start_formatted} - {item.end_formatted}
                              </span>
                              <span className={`text-xs font-semibold px-2 py-0.5 rounded ${
                                item.priority === 'primary' ? 'bg-red-100 text-red-700' :
                                item.priority === 'secondary' ? 'bg-yellow-100 text-yellow-700' :
                                'bg-gray-100 text-gray-700'
                              }`}>
                                {item.priority.toUpperCase()}
                              </span>
                              <span className="text-sm text-purple-600">{item.category}</span>
                            </div>
                            <p className="font-medium text-gray-800 mb-1">{item.observation}</p>
                            <p className="text-gray-700">{item.recommendation}</p>
                          </div>
                        ))}
                      </div>
                    </div>
                  )}

                  {/* Regular feedback (from frame mode) */}
                  {analysis.feedback && analysis.feedback.length > 0 && (
                    <div className="mb-6">
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
                  
                  {/* Drills (from agentic analysis) */}
                  {analysis.drills && analysis.drills.length > 0 && (
                    <div>
                      <h3 className="font-semibold text-lg mb-2">🏊 Recommended Drills</h3>
                      <ul className="list-disc pl-5 text-gray-700 space-y-1">
                        {analysis.drills.map((drill, idx) => (
                          <li key={idx}>{drill}</li>
                        ))}
                      </ul>
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